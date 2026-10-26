import asyncio
import base64
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, features
from prompt_toolkit.output import DummyOutput

from herdr_peek.images import (BACKGROUND, MAX_IMAGE_BYTES, ImageCanvas, ImageError,
                               ImagePreview, cell_pixels, decode_file, is_image, load_image)
import test_peek as peek_tests


class ImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def test_png_jpeg_webp_decode_in_isolated_worker_without_kitty(self):
        for kind in ('PNG', 'JPEG', 'WEBP'):
            if kind == 'WEBP' and not features.check('webp'):
                continue
            path = self.root/('中文 picture.'+kind.lower())
            Image.new('RGB', (1600, 800), '#ff5500').save(path, format=kind)
            self.assertTrue(is_image(path))
            with patch.dict(os.environ, {'PATH': ''}):
                preview = await load_image(path)
            self.assertEqual((preview.width, preview.height, preview.format), (1600, 800, kind))
            self.assertEqual(preview.pixels.size, (1200, 600))

    async def test_transparency_and_exif_orientation(self):
        path = self.root/'alpha.png'
        Image.new('RGBA', (20, 10), (255, 0, 0, 0)).save(path)
        preview = await load_image(path)
        self.assertEqual(preview.pixels.getpixel((0, 0)), (32, 36, 43))
        path = self.root/'rotated.jpg'
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (80, 40), 'red').save(path, exif=exif)
        preview = await load_image(path)
        self.assertEqual(preview.pixels.size, (40, 80))
        self.assertEqual((preview.width, preview.height), (40, 80))
        path = self.root/'gray16.png'
        Image.new('I;16', (20, 10), 32896).save(path)
        preview = await load_image(path)
        self.assertEqual(preview.pixels.getpixel((0, 0)), (128, 128, 128))

    async def test_large_jpeg_uses_reduced_decode_and_nonfiles_are_not_opened(self):
        path = self.root/'photo.jpg'
        Image.new('RGB', (6000, 4000), 'red').save(path)
        preview = await load_image(path)
        self.assertEqual((preview.width, preview.height), (6000, 4000))
        self.assertEqual(preview.pixels.size, (1200, 800))
        self.assertFalse(is_image(self.root))
        if hasattr(os, 'mkfifo'):
            path = self.root/'pipe.png'
            os.mkfifo(path)
            self.assertFalse(is_image(path))

    def test_limits_reject_before_full_decode_and_animation_is_not_played(self):
        path = self.root/'pixels.png'
        Image.new('RGB', (20, 20)).save(path)
        previous = Image.MAX_IMAGE_PIXELS
        self.addCleanup(setattr, Image, 'MAX_IMAGE_PIXELS', previous)
        with patch('herdr_peek.images.MAX_IMAGE_PIXELS', 200), \
                patch.object(Image.Image, 'load', side_effect=AssertionError('must not decode')):
            with self.assertRaisesRegex(ImageError, 'pixel_limit'):
                decode_file(path)
        with path.open('wb') as stream:
            stream.truncate(MAX_IMAGE_BYTES+1)
        with self.assertRaisesRegex(ImageError, 'file_limit'):
            decode_file(path)
        path = self.root/'animated.png'
        Image.new('RGB', (10, 10), 'red').save(path, save_all=True,
                                             append_images=[Image.new('RGB', (10, 10), 'blue')], duration=100)
        with self.assertRaisesRegex(ImageError, 'animated'):
            decode_file(path)

    async def test_corrupt_image_fails_without_leaking_worker_output(self):
        path = self.root/'bad.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\ntruncated')
        with self.assertRaisesRegex(ImageError, '^decode$'):
            await load_image(path)

    async def test_invalid_worker_output_is_a_preview_error(self):
        worker = self.root/'invalid.py'
        for reply in ('not json', '{}', '{"png": "broken", "width": 1, "height": 1, "format": "PNG"}'):
            worker.write_text(f'print({reply!r})\n')
            with patch('herdr_peek.images.__file__', str(worker)):
                with self.assertRaisesRegex(ImageError, '^decode$'):
                    await load_image(self.root/'unused')

    def test_text_ui_import_does_not_require_pillow(self):
        code = """import sys
class BlockPillow:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PIL' or fullname.startswith('PIL.'):
            raise ModuleNotFoundError('Pillow unavailable')
sys.meta_path.insert(0, BlockPillow())
from herdr_peek.peek import PeekUI
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parent.parent)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_cancel_and_timeout_kill_and_reap_real_worker(self):
        worker = self.root/'slow.py'
        worker.write_text("import os,signal,sys,time\nfrom pathlib import Path\n"
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                          "Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(30)\n")
        for timeout in (False, True, 'double_cancel'):
            marker = self.root/str(timeout)
            with patch('herdr_peek.images.__file__', str(worker)), \
                    patch('herdr_peek.images.IMAGE_TIMEOUT', .2 if timeout is True else 8):
                task = asyncio.create_task(load_image(marker))
                for _ in range(100):
                    if marker.exists():
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(marker.exists())
                pid = int(marker.read_text())
                if timeout is True:
                    with self.assertRaisesRegex(ImageError, 'timeout'):
                        await task
                else:
                    task.cancel()
                    if timeout == 'double_cancel':
                        await asyncio.sleep(0)
                        task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                for _ in range(100):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(.01)
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_same_canvas_drives_thumbnail_and_graphics_and_is_cached(self):
        path = self.root/'sample.png'
        Image.new('RGB', (100, 60), 'red').save(path)
        result = decode_file(path)
        preview = ImagePreview(base64.b64decode(result['png']), 100, 60, 'PNG')
        rendered = preview.render(30, 10, (8, 15))
        self.assertIs(preview.render(30, 10, (8, 15)), rendered)
        with Image.open(io.BytesIO(rendered.png)) as image:
            self.assertAlmostEqual(image.width / image.height, 240 / 150, delta=.02)
            self.assertEqual(image.width, 100)
        lines = ''.join(fragment[1] for fragment in rendered.fragments).splitlines()
        self.assertEqual(len(lines), 10)
        self.assertTrue(all(len(line) == 30 for line in lines))
        self.assertIsNot(preview.render(20, 10, None), rendered)
        self.assertIsNone(cell_pixels(DummyOutput()))

    def test_graphics_owns_one_id_and_reuploads_after_public_erase_screen(self):
        output = DummyOutput()
        commands = []
        output.write_raw = commands.append
        original = output.erase_screen
        canvas = ImageCanvas(output)
        image = type('Image', (), {'png': b'x'*10000})()
        canvas.show(image, 5, 3, 40, 12)
        first_id = canvas.image_id
        upload_count = sum('a=t,' in command for command in commands)
        count = len(commands)
        canvas.show(image, 5, 3, 40, 12)
        self.assertEqual(len(commands), count)
        canvas.show(image, 2, 3, 40, 12)
        self.assertEqual(sum('a=t,' in command for command in commands), upload_count)
        self.assertEqual(canvas.image_id, first_id)
        self.assertTrue(any('p=1,c=40,r=12,C=1,q=2' in command for command in commands))
        output.erase_screen()
        canvas.show(image, 2, 3, 40, 12)
        self.assertEqual(sum('a=t,' in command for command in commands), upload_count+1)
        canvas.close()
        self.assertEqual(output.erase_screen, original)
        deletes = [command for command in commands if 'a=d' in command]
        self.assertTrue(deletes)
        self.assertTrue(all('a=d,d=I,i=' in command for command in deletes))
        self.assertTrue(all('q=2' in command for command in commands))


class ImageUITests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = peek_tests.PeekUITests.asyncTearDown
    launch = peek_tests.PeekUITests.launch
    click = peek_tests.PeekUITests.click

    async def asyncSetUp(self):
        await peek_tests.PeekUITests.asyncSetUp(self)
        self.picture = self.root/'截图 preview.png'
        Image.new('RGB', (320, 180), '#ff5500').save(self.picture)

    async def wait_image(self):
        for _ in range(100):
            if self.ui.image is not None and not self.ui.busy:
                await asyncio.sleep(.05)
                return
            await asyncio.sleep(.02)
        self.fail(self.ui.body.text)

    async def test_portable_thumbnail_navigation_copy_and_back(self):
        await self.launch(text=f'"{self.picture}"\n"{self.file}"', columns=100)
        self.ui.app.ttimeoutlen = .01
        self.pipe.send_text('1')
        await self.wait_image()
        self.assertIs(self.ui.app.layout.current_control, self.ui.image_window.content)
        self.assertIsNone(self.ui.image_canvas.image_id)
        screen = self.ui.app.renderer._last_screen
        visible = ''.join(screen.data_buffer[y][x].char for y in range(24) for x in range(100))
        self.assertIn('▀', visible)
        self.pipe.send_text('\x06')
        await asyncio.sleep(.05)
        self.assertIs(self.ui.app.layout.current_control, self.ui.image_window.content)
        with patch('herdr_peek.peek.clipboard_write') as copy:
            self.ui.copy_path()
            await asyncio.sleep(.05)
            copy.assert_called_once_with(str(self.picture))
        self.pipe.send_text('\t\x14')
        await asyncio.sleep(.1)
        self.assertIs(self.ui.app.layout.current_control, self.ui.image_window.content)
        self.pipe.send_text('\x0a')
        await asyncio.sleep(.25)
        self.assertIsNone(self.ui.image)
        self.assertEqual(self.ui.body.text, self.file.read_text())
        self.pipe.send_text('\x0b')
        await self.wait_image()
        self.pipe.send_text('\x1b')
        await asyncio.sleep(.15)
        self.assertIsNone(self.ui.current)
        self.assertIsNone(self.ui.image)
        self.assertFalse(self.task.done())

    async def test_graphics_moves_with_sidebar_reuploads_on_clear_and_cleans_exit(self):
        commands = []
        with patch('herdr_peek.peek.cell_pixels', return_value=(8, 15)):
            await self.launch(text=f'"{self.picture}"\n"{self.file}"', columns=100)
            self.output.write_raw = commands.append
            self.pipe.send_text('1')
            await self.wait_image()
            self.assertIsNotNone(self.ui.image_canvas.image_id)
            self.assertTrue(any('a=t,' in command for command in commands))
            before = self.ui.image_window.position
            self.pipe.send_text('\x14')
            await asyncio.sleep(.15)
            after = self.ui.image_window.position
            self.assertLess(after.xpos, before.xpos)
            self.output.erase_screen()
            count = sum('a=t,' in command for command in commands)
            self.ui.app.invalidate()
            await asyncio.sleep(.1)
            self.assertEqual(sum('a=t,' in command for command in commands), count+1)
            deletes = sum('a=d,' in command for command in commands)
            self.pipe.send_text('\x0a')
            await asyncio.sleep(.25)
            self.assertIsNone(self.ui.image)
            self.assertGreater(sum('a=d,' in command for command in commands), deletes)
            self.pipe.send_text('\x0b')
            await self.wait_image()
            self.pipe.send_text('\x11')
            await asyncio.sleep(.15)
            self.assertTrue(self.task.done())
            self.assertIsNone(self.ui.image_canvas.image_id)
            self.assertTrue(any('a=d,d=I,i=' in command for command in commands))

    async def test_switch_cancels_image_decode_and_never_installs_stale_result(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def slow_image(path):
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                stopped.set()
        with patch('herdr_peek.peek.load_image', side_effect=slow_image):
            await self.launch(text=f'"{self.picture}"\n"{self.file}"')
            self.pipe.send_text('1')
            await asyncio.wait_for(started.wait(), 2)
            self.pipe.send_text('\x0a')
            await asyncio.wait_for(stopped.wait(), 2)
            await asyncio.sleep(.25)
            self.assertEqual(self.ui.current.path, self.file)
            self.assertEqual(self.ui.body.text, self.file.read_text())
            self.assertIsNone(self.ui.image)
