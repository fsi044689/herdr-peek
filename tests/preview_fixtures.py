"""本地合成预览样本，不读取用户文档。"""
import struct
import zipfile
import zlib
from pathlib import Path


def make_files(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    texts = {
        '中文 空格.txt': 'Herdr Peek 文本验收\n第二行中文',
        'code.py': 'print("Herdr Peek code preview")\n',
        'README.md': '# Herdr Peek Markdown\n\n**preview acceptance**\n',
        'page.html': '<!doctype html><html><head><title>Peek HTML</title></head><body><h1>Herdr Peek HTML preview</h1><p>Local file acceptance.</p></body></html>',
        'data.json': '{"preview": "Herdr Peek", "ok": true}\n',
        'image.svg': '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><rect width="240" height="160" fill="#0ea5e9"/><circle cx="120" cy="80" r="50" fill="#facc15"/></svg>',
    }
    for name, content in texts.items():
        (root/name).write_text(content)

    def chunk(kind, content):
        return struct.pack('!I', len(content)) + kind + content + struct.pack('!I', zlib.crc32(kind+content))
    rows = b''.join(b'\x00' + bytes([20, 150, 230])*240 for _ in range(160))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 240, 160, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
    (root/'image.png').write_bytes(png)
    stream = b'BT /F1 22 Tf 35 100 Td (Herdr Peek PDF preview) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    pdf, offsets = bytearray(b'%PDF-1.4\n'), [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f'{number} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(pdf)
    pdf.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        pdf.extend(f'{offset:010} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    (root/'document.pdf').write_bytes(pdf)
    with zipfile.ZipFile(root/'archive.zip', 'w') as archive:
        archive.writestr('note.txt', 'Herdr Peek archive sample')
    (root/'binary.bin').write_bytes(b'\x00\xff\x01Herdr Peek binary sample')
    return [root/name for name in [*texts, 'image.png', 'document.pdf', 'archive.zip', 'binary.bin']]
