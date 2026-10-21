# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from importlib.metadata import distributions

root = Path(SPECPATH)
datas = collect_data_files('ddddocr') + collect_data_files('playwright')
datas += [(str(root / '使用说明.md'), '.')]
datas += [(str(root / 'webassistant/assets/mock_exam'), 'webassistant/assets/mock_exam')]
for distribution in distributions():
    for item in distribution.files or []:
        parts = tuple(str(p).lower() for p in item.parts)
        if any(p.endswith('.dist-info') for p in parts) and any('license' in p or p in ('copying', 'notice', 'authors') for p in parts):
            file = Path(distribution.locate_file(item))
            if file.is_file():
                datas.append((str(file), 'licenses/' + distribution.metadata['Name']))
chrome = root / 'tools/chrome'
if chrome.exists():
    datas.append((str(chrome), 'chrome'))
binaries = collect_dynamic_libs('onnxruntime')
a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries,
    datas=datas, hiddenimports=['ddddocr', 'onnxruntime', 'PIL', 'pdfplumber',
    'openpyxl', 'playwright.async_api'], hookspath=[], runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'notebook', 'pytest'], noarchive=False)
# Qt uses Windows' versionless ICU API. A PATH entry from another tool can
# cause bindepend to collect its incompatible, version-suffixed ICU build.
# Keep the OS ICU resolution used by the working source application.
a.binaries = [entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Answering',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Answering')
