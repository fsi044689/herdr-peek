import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.utils import get_cwidth

from herdr_peek.core import PeekError, resolve_path
from herdr_peek.screen import Reference, Screen, reference_spans


class PathMatrixTests(unittest.TestCase):
    def test_symlink_loop_does_not_hide_a_following_real_file(self):
        loop = self.root/'loop.py'
        loop.symlink_to(loop.name)
        file = self.file('real.py')
        for target in (str(loop), loop.as_uri(), str(loop)+':12'):
            with self.subTest(target=target):
                self.assertEqual([reference.path for reference, _, _ in
                                  reference_spans(target+' '+str(file), self.root)], [file])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def file(self, name):
        file = self.root/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('preview\nsecond\n', encoding='utf-8')
        return file

    def assert_reference(self, text, file, source, line=None):
        found = list(reference_spans(text, self.root))
        self.assertEqual([r for r, _, _ in found], [Reference(file, line)])
        self.assertEqual([text[a:b] for _, a, b in found], [source])

    def test_filename_and_wrapper_matrix(self):
        names = ['plain.py', '中文.md', 'v8.12.10 App前端对接.md',
                 '目录 空格/文件 name.md', 'folder(1)/report [copy].md',
                 'dir（中文）/v1.2 file.md', 'file,name.txt', 'semi;colon.py',
                 'hash#name.md', "quote'name.txt", 'double"name.txt',
                 'back`tick.txt', '.env', 'Makefile', 'name:12',
                 'trailing.txt.', '-leading.txt', 'emoji🚀/计划.md']
        for name in names:
            file = self.file(name)
            for left, right in [('`', '`'), ('"', '"'), ("'", "'"), ('[文档](', ')')]:
                if left in ('`', '"', "'") and left in name:
                    continue
                with self.subTest(name=name, wrapper=left):
                    self.assert_reference(f'修改 {left}{file}{right} 已完成', file, str(file))

    def test_bare_absolute_special_characters(self):
        for name in ['Makefile', '.env', 'folder(1)/file[copy].py', '中文（新版）.md',
                     'file,name.txt', 'semi;colon.py', "quote'name.txt", 'trailing.txt.']:
            file = self.file(name)
            with self.subTest(name=name):
                self.assert_reference(f'文件 {file} 已完成。', file, str(file))

    def test_relative_home_and_uri_forms(self):
        file = self.file('目录 空格/v8.12 App前端.md')
        relative = file.relative_to(self.root)
        forms = [f'`{relative}`', f'./{relative}', f'../{self.root.name}/{relative}',
                 f'~/{relative}', file.as_uri(), file.as_uri().replace('file:///', 'file://localhost/')]
        with patch('pathlib.Path.home', return_value=self.root), patch.dict('os.environ', {'HOME': str(self.root)}):
            for text in forms:
                with self.subTest(form=text):
                    source = text.strip('`')
                    self.assert_reference(text, file, source)
        for name in ('Makefile', '.env', '目录/README', 'cafe\u0301.md'):
            relative_file = self.file(name)
            with self.subTest(relative=name):
                self.assert_reference(name, relative_file, name)

    def test_bare_encoded_file_uri_cli_wrap_at_every_path_position(self):
        file = self.file('中文 空格.txt')
        for uri in (file.as_uri(), file.as_uri().replace('file:///', 'file://localhost/')):
            for cut in range(uri.index('/', len('file://'))+1, len(uri)):
                with self.subTest(uri=uri, cut=cut):
                    source = uri[:cut]+'\n  '+uri[cut:]
                    self.assert_reference(source, file, source)
        uri = file.as_uri()+'#L2'
        a, b = uri.index('%')+2, uri.index('%')+12
        source = uri[:a]+'\n  '+uri[a:b]+'\n    '+uri[b:]
        self.assert_reference(source, file, source, 2)

    def test_file_uri_continuation_prefers_uri_over_same_relative_worktree_file(self):
        local = self.file('src/main.py')
        other = self.file('worktree/src/main.py')
        source = (self.root/'worktree').as_uri()+'/\n  src/main.py'
        self.assert_reference(source, other, source)

    def test_file_uri_wrap_does_not_cross_blank_unindented_or_other_target(self):
        file = self.file('中文 空格.txt')
        uri = file.as_uri()
        cut = uri.index('%')+2
        for separator in ('\n', '\n\n  ', '\n  \n  '):
            self.assertEqual(list(reference_spans(uri[:cut]+separator+uri[cut:], self.root)), [])
        other = self.file('other.txt')
        self.assertEqual([r.path for r, _, _ in reference_spans(file.as_uri()+'\n  '+str(other), self.root)], [file, other])
        self.assertEqual([r.path for r, _, _ in reference_spans(self.root.as_uri()+'\n  '+str(other), self.root)], [other])

    def test_shell_escaped_spaces_preserve_the_source_span(self):
        file = self.file('目录 空格/App 文档.md')
        for path in (str(file), './'+str(file.relative_to(self.root))):
            source = path.replace(' ', '\\ ')
            with self.subTest(source=source):
                self.assert_reference('修改 '+source+' 已完成', file, source)
        literal = self.file('目录\\ name.md')
        self.assert_reference(str(literal), literal, str(literal))

    def test_visible_paths_with_osc8_and_colors_keep_their_positions(self):
        file = self.file('中文文档.md')
        path = str(file)
        for ansi in [f'\x1b]8;;{file.as_uri()}\x1b\\{path}\x1b]8;;\x1b\\',
                     f'\x1b]8;;{file.as_uri()}\x07{path}\x1b]8;;\x07',
                     '\x1b[31m'+path[:10]+'\x1b[32m'+path[10:]+'\x1b[0m']:
            with self.subTest(ansi=ansi[:15]):
                screen = Screen({'frames': [{'x': 4, 'y': 2, 'ansi': ansi, 'source': True}],
                                 'text': path, 'size': [200, 8], 'notice': ''}, self.root)
                self.assertEqual(screen.hits, [Reference(file)])
                self.assertEqual(screen.cells(200, 8)[2][4][2], 0)

    def test_duplicate_occurrences_and_later_filesystem_changes(self):
        file = self.root/'late.py'
        source = f'{file}\n{file}\n{file}'
        self.assertEqual(list(reference_spans(source, self.root)), [])
        file.write_text('preview')
        self.assertEqual([r.path for r, _, _ in reference_spans(source, self.root)], [file]*3)
        file.unlink()
        self.assertEqual(list(reference_spans(source, self.root)), [])

    def test_symlink_directory_and_literal_longer_filename_are_not_confused(self):
        short = self.file('version.md')
        longer = self.file('version.md App.md')
        self.assert_reference(str(longer), longer, str(longer))
        alias = self.root/'alias.md'
        alias.symlink_to(short)
        self.assert_reference(str(alias), short, str(alias))
        alias.unlink()
        alias.symlink_to(self.root/'missing.md')
        self.assertEqual(list(reference_spans(str(alias), self.root)), [])
        self.assertEqual(list(reference_spans(str(self.root), self.root)), [])

    def test_chinese_colon_before_absolute_path(self):
        file = self.file('中文.md')
        self.assert_reference(f'修改：{file}', file, str(file))

    def test_line_suffixes_and_literal_suffix_filenames(self):
        file = self.file('source.py')
        for suffix, line in [(':12', 12), (':12:4', 12), ('#L12', 12), ('#L12-L15', 12)]:
            for template in ['{}', '`{}`', '[文件]({})']:
                source = str(file)+suffix
                with self.subTest(suffix=suffix, template=template):
                    self.assert_reference(template.format(source), file, source, line)
        for name in ['source.py:12', 'source.py#L12', 'trailing.txt.']:
            literal = self.file(name)
            with self.subTest(literal=name):
                self.assert_reference(f'`{literal}`', literal, str(literal))
        for suffix in ('#L12', '#L12-L15'):
            source = file.as_uri()+suffix
            with self.subTest(uri=suffix):
                self.assert_reference(source, file, source, 12)

    def test_markdown_parentheses_and_angle_destinations(self):
        file = self.file('folder(a(b))/App 文档.md')
        for text, source in [(f'[文件]({file})', str(file)),
                             (f'[文件](<{file}>)', str(file)),
                             (f'({file})', str(file)),
                             (f'![图片]({file.as_uri()})', file.as_uri())]:
            with self.subTest(form=text):
                self.assert_reference(text, file, source)

    def test_neighboring_paths_and_missing_paths(self):
        first, second = self.file('v1.2 App.md'), self.file('other.txt')
        for delimiter in (' ', ', ', '，', '; ', '；', ') (', '\n'):
            text = f'{first}{delimiter}{second}'
            with self.subTest(separator=delimiter):
                found = list(reference_spans(text, self.root))
                self.assertEqual([r.path for r, _, _ in found], [first, second])
                self.assertEqual([text[a:b] for _, a, b in found], [str(first), str(second)])
        for text in [f'/missing/first.py {second}', f'没有文件 /missing/file.py；{second}',
                     f'普通句子 ({second}) 后面没有文件。']:
            with self.subTest(text=text):
                self.assert_reference(text, second, str(second))

    def test_non_files_remote_uris_and_hard_lines_are_not_joined(self):
        file = self.file('version App前端.md')
        texts = [str(self.root), str(self.root/'missing.py'),
                 'file://remote'+str(file),
                 '(' + str(file).replace('App前端', 'App前\n端') + ')',
                 '(' + str(file).replace('App前端', 'App前\n\n  端') + ')']
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(list(reference_spans(text, self.root)), [])
        # URL 不能因项目中恰好有同形目录，就被当成本机路径。
        self.file('https:/example.test/data.txt')
        with self.assertRaises(PeekError):
            resolve_path('https://example.test/data.txt', self.root)

    def test_every_soft_wrap_position_and_ansi_color_maps_to_same_file(self):
        file = self.file('中文目录/v8.12.10 App前端对接.md')
        path = str(file)
        logical = f'日志 `\x1b[32m{path}\x1b[0m` 完成'
        plain = f'日志 `{path}` 完成'
        for cut in range(1, len(path)):
            rendered = logical.replace(path, path[:cut]+'\n'+path[cut:])
            data = {'frames': [{'x': 0, 'y': 0, 'ansi': rendered, 'source': True}],
                    'text': plain, 'size': [200, 8], 'notice': ''}
            with self.subTest(cut=cut):
                screen = Screen(data, self.root)
                self.assertEqual(screen.hits, [Reference(file)])
                cells = screen.cells(200, 8)
                self.assertEqual(cells[1][0][2], 0)
                self.assertEqual(sum(get_cwidth(c[1]) for row in cells for c in row if c and c[2] == 0),
                                 get_cwidth(path))

    def test_multiple_cli_indented_wraps_keep_original_positions(self):
        file = self.file('folder name/v8.12.10 App前端对接.md')
        path = str(file)
        for indent in ('  ', '    ', '\t'):
            text = '('+path.replace('folder name/', 'folder name/\n'+indent).replace('App前端', 'App前\n'+indent+'端')+')'
            with self.subTest(indent=repr(indent)):
                self.assert_reference(text, file, text[1:-1])


if __name__ == '__main__':
    unittest.main()
