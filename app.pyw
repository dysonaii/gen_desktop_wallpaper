import ctypes
import json
import os
import tempfile

import PySimpleGUI as sg
from PIL import Image, ImageDraw, ImageFont

try:  # ponytail: Tk 原生不吃檔案拖放，有裝才啟用，失敗就退回「選擇」按鈕
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except Exception:
    _HAS_DND = False

W, H = 1920, 1080
OUT = os.path.join(os.path.expanduser('~'), 'Pictures', 'wallpaper.png')
CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')


def load_cfg():
    try:
        with open(CFG, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cfg(d):
    try:
        with open(CFG, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
    except OSError:
        pass


def font(size):
    # ponytail: 拿系統字型，中文免裝字型檔
    for p in (r'C:\Windows\Fonts\msjh.ttc', r'C:\Windows\Fonts\arial.ttf'):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


_last_rects = []  # 每層小圖位置 [(rect, idx)]，給預覽拖曳做 hit-test
_last_text_rect = None  # 文字 bbox，給預覽選中框


def compose(text, color, layers, size=80, text_xy=(960, 200), text_op=100):
    # ponytail: 座標一律中心點；RGBA 合一層，透明度不用分支；壞圖單層跳過
    global _last_rects, _last_text_rect
    _last_rects = []
    _last_text_rect = None
    base = Image.new('RGBA', (W, H), (0, 0, 0, 255))
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    for idx, L in enumerate(layers):
        p = L.get('path')
        if not p or not os.path.isfile(p):
            continue
        try:
            small = Image.open(p).convert('RGBA')
        except Exception:
            continue
        op = L.get('op', 100)
        w = max(1, round(min(small.width, W // 2) * L.get('scale', 100) / 100))
        h = round(small.height * w / small.width)
        small = small.resize((w, h)).rotate(L.get('rot', 0), expand=True, resample=Image.BICUBIC)
        small.putalpha(small.getchannel('A').point(lambda v: v * op // 100))
        w, h = small.size
        cx, cy = L.get('xy') or (W // 2, H // 2)
        x0, y0 = round(cx - w / 2), round(cy - h / 2)
        layer.paste(small, (x0, y0))  # 無 mask 直接蓋，透明層上等同混合，只算一次 alpha
        _last_rects.append(((x0, y0, x0 + w, y0 + h), idx))
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    f = font(size)
    d = ImageDraw.Draw(layer)
    if text:
        _last_text_rect = d.textbbox(text_xy, text, font=f, anchor='mm')
    d.text(text_xy, text, fill=(r, g, b, round(255 * text_op / 100)), font=f, anchor='mm')
    return Image.alpha_composite(base, layer).convert('RGB')


def set_wallpaper(path):
    ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3)


def clamp_xy(xy, w, h):
    return (min(max(xy[0], 0), w), min(max(xy[1], 0), h))


def parse_wh(s):
    try:  # '2560x1080' / '2560x1080（偵測）' / 自打 '1234x567'，半成品回 None
        parts = s.split('（')[0].lower().split('x')
        w, h = int(parts[0]), int(parts[1])
        return (w, h) if len(parts) == 2 and w > 0 and h > 0 else None
    except (ValueError, IndexError, AttributeError):
        return None


def main():
    sg.theme('DarkBlack1')
    global W, H
    try:  # ponytail: 主螢幕尺寸當預設，失敗回退 1920x1080
        u = ctypes.windll.user32
        detW, detH = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        spanW, spanH = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    except Exception:
        detW, detH = 1920, 1080
        spanW, spanH = 0, 0
    if detW < 1 or detH < 1:
        detW, detH = 1920, 1080
    cfg = load_cfg()
    W, H = cfg.get('W') or detW, cfg.get('H') or detH
    if W < 1 or H < 1:
        W, H = detW, detH
    PW, PH = 960, round(960 * H / W)
    PRESETS = []
    cands = [f'{detW}x{detH}（偵測）']
    if spanW > detW or spanH > detH:
        cands.append(f'{spanW}x{spanH}（橫跨）')
    for o in cands + ['1920x1080', '2560x1080', '2560x1440', '3840x2160', '1366x768']:
        if o.split('（')[0] not in [p.split('（')[0] for p in PRESETS]:
            PRESETS.append(o)
    S = {'text_xy': tuple(cfg.get('text_xy') or (W // 2, 200)), 'size': cfg.get('size', 80),
         'text_op': cfg.get('text_op', 100), 'sel': 0, 'active': cfg.get('active', 'text'),
         'imgs': cfg.get('imgs') or []}
    if not S['imgs'] and cfg.get('image'):  # 舊版單圖設定搬過來
        S['imgs'] = [{'path': cfg['image'], 'xy': cfg.get('img_xy'),
                      'scale': cfg.get('img_scale', 100), 'rot': cfg.get('img_rot', 0),
                      'op': cfg.get('img_op', 100)}]
    for L in S['imgs']:
        L.setdefault('scale', 100)
        L.setdefault('rot', 0)
        L.setdefault('op', 100)
        L.setdefault('xy', None)
        if L['xy']:
            L['xy'] = clamp_xy(tuple(L['xy']), W, H)
    S['text_xy'] = clamp_xy(S['text_xy'], W, H)
    if S['active'] != 'text' and not (isinstance(S['active'], int) and 0 <= S['active'] < len(S['imgs'])):
        S['active'] = 'text'
    L0 = S['imgs'][0] if S['imgs'] else {}
    lay = [
        [sg.Text('文字'), sg.Input(cfg.get('text', 'Hello'), key='-T-', enable_events=True),
         sg.ColorChooserButton('顏色', key='-CC-', target='-C-'),
         sg.Input(cfg.get('color', '#ffffff'), key='-C-', size=8, enable_events=True),
         sg.Text('小圖'), sg.Listbox([os.path.basename(L['path']) for L in S['imgs']], size=(24, 2),
         key='-L-', enable_events=True),
         sg.Button('加入'), sg.Button('移除'),
         sg.Text('尺寸'), sg.Combo(PRESETS, default_value=f'{W}x{H}', key='-WH-', size=(16, 1),
         enable_events=True)],
        [sg.Text('字級'), sg.Slider((20, 200), S['size'], orientation='h', size=(12, 15), key='-FS-', enable_events=True),
         sg.Text('文字透明'), sg.Slider((0, 100), S['text_op'], orientation='h', size=(12, 15), key='-TO-', enable_events=True),
         sg.Text('小圖透明'), sg.Slider((0, 100), L0.get('op', 100), orientation='h', size=(12, 15), key='-IO-', enable_events=True),
         sg.Text('小圖縮放'), sg.Slider((10, 200), L0.get('scale', 100), orientation='h', size=(12, 15), key='-IS-', enable_events=True),
         sg.Text('小圖旋轉'), sg.Slider((-180, 180), L0.get('rot', 0), orientation='h', size=(12, 15), key='-IR-', enable_events=True),],
        #[sg.Text('（小圖可從檔案總管拖入；排版在預覽圖拖曳）')],
        [sg.Frame(f'桌面預覽 {W}x{H}', [[sg.Image(key='-V-', size=(PW, PH))]], key='-F-')],
        [sg.Button('下載桌布'), sg.Button('設為桌布')],
    ]
    win = sg.Window('桌布產生器', lay, finalize=True)

    def snap():
        return {'text': win['-T-'].get(), 'color': win['-C-'].get(), 'W': W, 'H': H,
                'size': S['size'], 'text_op': S['text_op'], 'text_xy': list(S['text_xy']),
                'active': S['active'],
                'imgs': [{'path': L['path'], 'xy': list(L['xy']) if L['xy'] else None,
                          'scale': L['scale'], 'rot': L['rot'], 'op': L['op']} for L in S['imgs']]}

    def current():
        t = win['-T-'].get()
        c = win['-C-'].get().strip() or '#ffffff'
        try:
            return compose(t, c, S['imgs'], S['size'], S['text_xy'], S['text_op'])
        except Exception:  # 打字中的半成品色碼就退回白字，不洗版報錯
            return compose(t, '#ffffff', [], S['size'])

    def refresh():
        p = os.path.join(tempfile.gettempdir(), 'wall_prev.png')
        prev = current().resize((PW, PH))
        k = PW / W
        for r, idx in _last_rects:  # 選中的小圖描淡黃框，只在預覽，不進存檔
            if idx == S['active']:
                ImageDraw.Draw(prev).rectangle([round(v * k) for v in r], outline=(255, 255, 153), width=3)
        if S['active'] == 'text' and _last_text_rect:  # 選中的文字描淡綠框
            ImageDraw.Draw(prev).rectangle([round(v * k) for v in _last_text_rect],
                                           outline=(153, 255, 153), width=3)
        prev.save(p)
        win['-V-'].update(filename=p)

    def move(ev):
        return round(min(max(ev.x * W / PW, 0), W)), round(min(max(ev.y * H / PH, 0), H))

    def sel():
        return S['imgs'][S['sel']] if S['imgs'] else None

    def names():
        return [os.path.basename(L['path']) for L in S['imgs']]

    def sync_sliders():
        L = sel() or {}
        win['-IO-'].update(value=L.get('op', 100))
        win['-IS-'].update(value=L.get('scale', 100))
        win['-IR-'].update(value=L.get('rot', 0))

    def add_files(files):
        paths = [L['path'] for L in S['imgs']]
        n = 0
        for f in files:
            if f and f not in paths and os.path.isfile(f):
                S['imgs'].append({'path': f, 'xy': None, 'scale': 100, 'rot': 0, 'op': 100})
                paths.append(f)
                n += 1
        if n:
            S['sel'] = len(S['imgs']) - 1
            S['active'] = S['sel']
            win['-L-'].update(values=names(), set_to_index=[S['sel']])
            sync_sliders()
        return n

    def press(ev):
        fx, fy = move(ev)
        for r, idx in reversed(_last_rects):  # 上層先中
            if r[0] <= fx <= r[2] and r[1] <= fy <= r[3]:
                S['sel'] = idx
                S['active'] = idx
                win['-L-'].update(set_to_index=[idx])
                sync_sliders()
                S['drag'] = idx
                refresh()
                return
        S['text_xy'] = (fx, fy)
        S['active'] = 'text'
        S['drag'] = 'text'
        refresh()

    def motion(ev):
        d = S.get('drag', 'text')
        if d == 'text':
            S['text_xy'] = move(ev)
        elif isinstance(d, int) and d < len(S['imgs']):
            S['imgs'][d]['xy'] = move(ev)
        refresh()

    w = win['-V-'].Widget
    w.bind('<ButtonPress-1>', press)
    w.bind('<B1-Motion>', motion)
    w.bind('<ButtonRelease-1>', lambda ev: S.pop('drag', None))

    def drop(data):
        try:
            files = win.TKroot.tk.splitlist(data)
        except Exception:
            return
        if add_files(files):
            refresh()

    if _HAS_DND:
        try:  # 不換掉 tkinter.Tk（會無窮遞迴），只給現成 root 載入 tkdnd 並掛上方法
            TkinterDnD._require(win.TKroot)
            for _m in ('drop_target_register', 'dnd_bind'):
                setattr(win.TKroot, _m, getattr(TkinterDnD.DnDWrapper, _m).__get__(win.TKroot))
            win.TKroot.drop_target_register(DND_FILES)
            win.TKroot.dnd_bind('<<Drop>>', lambda e: drop(e.data))
        except Exception:
            pass

    refresh()
    while True:
        e, v = win.read()
        if e in (sg.WIN_CLOSED, None):
            save_cfg(snap())
            break
        if e in ('-T-', '-C-', '-CC-'):
            refresh()
        elif e == '-WH-':
            wh = parse_wh(v['-WH-'])
            if wh:  # 打字中半成品（None）先忽略
                W, H = wh
                PW, PH = 960, round(960 * H / W)
                win['-F-'].update(value=f'桌面預覽 {W}x{H}')
                win['-V-'].Widget.config(width=PW, height=PH)
                S['text_xy'] = clamp_xy(S['text_xy'], W, H)
                for L in S['imgs']:
                    if L['xy']:
                        L['xy'] = clamp_xy(L['xy'], W, H)
                save_cfg(snap())
                refresh()
        elif e == '加入':  # 不用 FilesBrowse：它預設 target 指到左邊 Listbox，路徑寫不回來
            f = sg.popup_get_file('選小圖', multiple_files=True,
                                  file_types=(('圖片', '*.png *.jpg *.jpeg *.gif *.bmp *.webp'),))
            if f and add_files(f if isinstance(f, (tuple, list)) else f.split(';')):
                refresh()
        elif e == '移除':
            if S['imgs']:
                S['imgs'].pop(S['sel'])
                S['sel'] = max(0, min(S['sel'], len(S['imgs']) - 1))
                S['active'] = S['sel'] if S['imgs'] else 'text'
                win['-L-'].update(values=names())
                sync_sliders()
                refresh()
        elif e == '-L-':
            idx = win['-L-'].get_indexes()
            if idx:
                S['sel'] = idx[0]
                S['active'] = S['sel']
                sync_sliders()
        elif e in ('-FS-', '-TO-'):
            S['size'], S['text_op'] = int(v['-FS-']), int(v['-TO-'])
            refresh()
        elif e in ('-IO-', '-IS-', '-IR-'):
            L = sel()
            if L:
                L['op'], L['scale'], L['rot'] = int(v['-IO-']), int(v['-IS-']), int(v['-IR-'])
                refresh()
        elif e == '下載桌布':
            f = sg.popup_get_file('存檔', save_as=True, default_path=OUT,
                                  file_types=(('PNG', '*.png'),))
            if f:
                current().save(f)
                save_cfg(snap())
                sg.popup('已存到 ' + f)
        elif e == '設為桌布':
            current().save(OUT)
            set_wallpaper(OUT)
            save_cfg(snap())
            sg.popup('已設為桌布')
    win.close()


if __name__ == '__main__':
    main()
