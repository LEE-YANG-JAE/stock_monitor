# stock_monitor_gui.spec

# Import the necessary modules
block_cipher = None

a = Analysis(
    ['stock_monitor_gui.py'],  # Main script
    pathex=['.'],  # Current directory
    binaries=[],  # No binary files to include
    datas=[],  # We will add any data files below if necessary
    hiddenimports=['stock_score', 'config', 'market_trend_manager'],  # Explicitly include stock_score.py as a hidden import
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='stock_monitor_gui',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
