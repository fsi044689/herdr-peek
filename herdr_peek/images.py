from __future__ import annotations

import asyncio
import base64
import io
import json
import secrets
import struct
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_JPEG_PIXELS = 100_000_000
PREVIEW_EDGE = 1200
IMAGE_TIMEOUT = 8
BACKGROUND = '#20242b'


class ImageError(Exception):
    pass


def is_image(path):
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        header = stream.read(12)
    return (header.startswith(b'\x89PNG\r\n\x1a\n') or header.startswith(b'\xff\xd8\xff')
            or (header[:4] == b'RIFF' and header[8:12] == b'WEBP'))


def cell_pixels(output):
    try:
        import fcntl
        import termios
        rows, columns, width, height = struct.unpack(
            'HHHH', fcntl.ioctl(output.fileno(), termios.TIOCGWINSZ, b'\0' * 8))
        if rows and columns and width and height:
            return width / columns, height / rows
    except (ImportError, OSError, ValueError, NotImplementedError):
        pass
    return None


def decode_file(path):
    from PIL import Image, ImageOps

    if not path.is_file():
        raise ImageError('not_file')
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ImageError('file_limit')
    Image.MAX_IMAGE_PIXELS = MAX_JPEG_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(path, formats=('PNG', 'JPEG', 'WEBP')) as source:
                width, height = source.size
                kind = source.format
                limit = MAX_JPEG_PIXELS if kind == 'JPEG' else MAX_IMAGE_PIXELS
                if width * height > limit:
                    raise ImageError('pixel_limit')
                if getattr(source, 'is_animated', False):
                    raise ImageError('animated')
                if source.getexif().get(0x0112) in (5, 6, 7, 8):
                    width, height = height, width
                source.draft('RGB', (PREVIEW_EDGE, PREVIEW_EDGE))
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ImageError('pixel_limit')
                source.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
                oriented = ImageOps.exif_transpose(source)
                if oriented.mode.startswith('I'):
                    oriented = oriented.point(lambda value: value / 256).convert('L')
                if oriented.mode in ('RGBA', 'LA') or 'transparency' in oriented.info:
                    background = Image.new('RGBA', oriented.size, BACKGROUND)
                    background.alpha_composite(oriented.convert('RGBA'))
                    oriented = background.convert('RGB')
                else:
                    oriented = oriented.convert('RGB')
                output = io.BytesIO()
                oriented.save(output, format='PNG')
                return {'png': base64.b64encode(output.getvalue()).decode('ascii'),
                        'width': width, 'height': height, 'format': kind}
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise ImageError('pixel_limit') from exc


async def load_image(path):
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-I', str(Path(__file__).resolve()), str(path),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        try:
            output, _ = await asyncio.wait_for(process.communicate(), IMAGE_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ImageError('timeout') from exc
        if process.returncode:
            raise ImageError('decode')
        try:
            result = json.loads(output)
            if 'error' in result:
                raise ImageError(result['error'])
            return ImagePreview(base64.b64decode(result['png']), result['width'], result['height'], result['format'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise ImageError('decode') from exc
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


@dataclass
class ImagePreview:
    png: bytes
    width: int
    height: int
    format: str

    def __post_init__(self):
        from PIL import Image

        with Image.open(io.BytesIO(self.png)) as image:
            self.pixels = image.convert('RGB')
        self._rendered = None
        self._size = None

    def render(self, columns, rows, pixels=None):
        from PIL import Image

        columns, rows = max(1, columns), max(1, rows)
        size = columns, rows, pixels
        if size != self._size:
            cell_width, cell_height = pixels or (1, 2)
            aspect = columns * cell_width / (rows * cell_height)
            width = max(self.pixels.width, self.pixels.height * aspect)
            height = max(self.pixels.height, self.pixels.width / aspect)
            scale = min(1, PREVIEW_EDGE / max(width, height))
            canvas = Image.new('RGB', (max(1, round(width * scale)), max(1, round(height * scale))), BACKGROUND)
            image = self.pixels.copy()
            image.thumbnail(canvas.size)
            canvas.paste(image, ((canvas.width-image.width)//2, (canvas.height-image.height)//2))
            output = io.BytesIO()
            canvas.save(output, format='PNG')
            thumbnail = canvas.resize((columns, rows * 2))
            values = thumbnail.load()
            fragments = []
            for y in range(0, thumbnail.height, 2):
                for x in range(thumbnail.width):
                    top = '#%02x%02x%02x' % values[x, y]
                    bottom = '#%02x%02x%02x' % values[x, y+1]
                    fragments.append((f'fg:{top} bg:{bottom}', '▀'))
                fragments.append(('', '\n'))
            self._rendered = RenderedImage(output.getvalue(), fragments[:-1])
            self._size = size
        return self._rendered


@dataclass
class RenderedImage:
    png: bytes
    fragments: list


class ImageCanvas:
    def __init__(self, output):
        self.output = output
        self.image = None
        self.image_id = None
        self.placement = None
        self._erase_screen = output.erase_screen

        def erase_screen():
            self._erase_screen()
            self.image = None
            self.placement = None

        self._on_erase = erase_screen
        output.erase_screen = erase_screen

    def close(self):
        self.clear()
        if self.output.erase_screen is self._on_erase:
            self.output.erase_screen = self._erase_screen

    def clear(self):
        if self.image_id is not None:
            self.output.write_raw(f'\x1b_Ga=d,d=I,i={self.image_id},q=2;\x1b\\')
            self.output.flush()
        self.image = self.image_id = None
        self.placement = None

    def show(self, image, x, y, columns, rows):
        if columns < 1 or rows < 1:
            self.clear()
            return
        if image is not self.image:
            self.clear()
            self.image, self.image_id = image, secrets.randbelow(2**31-1) + 1
            data = base64.b64encode(image.png).decode('ascii')
            for offset in range(0, len(data), 4096):
                prefix = f'a=t,f=100,i={self.image_id},q=2,' if offset == 0 else 'q=2,'
                more = int(offset + 4096 < len(data))
                self.output.write_raw(f'\x1b_G{prefix}m={more};{data[offset:offset+4096]}\x1b\\')
        placement = x, y, columns, rows
        if placement == self.placement:
            return
        self.placement = placement
        self.output.write_raw(
            f'\x1b7\x1b[{y+1};{x+1}H\x1b_Ga=p,i={self.image_id},p=1,c={columns},r={rows},C=1,q=2;\x1b\\\x1b8'
        )
        self.output.flush()


if __name__ == '__main__':
    try:
        result = decode_file(Path(sys.argv[1]))
    except ImageError as exc:
        result = {'error': str(exc)}
    except Exception:
        result = {'error': 'decode'}
    print(json.dumps(result))
