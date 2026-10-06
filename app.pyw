import ctypes
import json
import os
import tempfile
import time
import winreg

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
STYLES = {'填滿': ('10', '0'), '全螢幕': ('6', '0'), '延展': ('2', '0'),
          '並排': ('0', '1'), '置中': ('0', '0'), '跨螢幕': ('22', '0')}


def load_cfg():
    # 讀設定檔；不存在或壞掉回空 dict
    try:
        with open(CFG, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cfg(d):
    # 寫設定檔；失敗靜默略過（不擋主流程）
    try:
        with open(CFG, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
    except OSError:
        pass


def font(size):
    # 依字級取系統中文字型，免裝字型檔
    # ponytail: 拿系統字型，中文免裝字型檔
    for p in (r'C:\Windows\Fonts\msjh.ttc', r'C:\Windows\Fonts\arial.ttf'):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


_last_rects = []  # 每層小圖位置 [(rect, idx)]，給預覽拖曳做 hit-test
_last_text_rects = []  # 每組文字 bbox [(rect, idx)]，給預覽選中框＋點選用


def draw_doodles(d, doodles):
    # 把塗鴉筆跡畫到 Draw 上（合成／轉圖層共用）
    for s in doodles:
        pts = s.get('pts') or []
        if not pts:
            continue
        try:
            dc = tuple(int(s.get('color', '#ffffff')[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            dc = (255, 255, 255)
        if len(pts) > 1:
            d.line(pts, fill=dc + (255,), width=8, joint='curve')
        else:
            x, y = pts[0]
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=dc + (255,))


def render_doodles(doodles):
    # 塗鴉轉透明底 PNG（轉圖層用）
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw_doodles(ImageDraw.Draw(img), doodles)
    return img


def compose(texts, layers, doodles=(), _text=None, _color=None, size=80, text_xy=(960, 200), text_op=100):
    # 合成全尺寸桌布，回傳 RGB 圖；順手記錄各層位置供拖曳/框線用
    # ponytail: 座標一律中心點；RGBA 合一層，透明度不用分支；壞圖單層跳過
    # texts 為 [{'text','color','size','xy','op'}]；舊單組參數 (_text/_color/size/text_xy/text_op) 沒給 texts 時自動包一組
    global _last_rects, _last_text_rects
    _last_rects = []
    _last_text_rects = []
    if texts is None:
        texts = [{'text': _text or '', 'color': _color or '#ffffff',
                  'size': size, 'xy': tuple(text_xy), 'op': text_op}] if _text else []
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
        if L.get('key'):  # 去白底：四角 flood fill 吃連通背景白；框線圍住的白（如眼睛）吃不到
            # ponytail: thresh=40 是 G/O/Y.jpg 實測值（20 吃不掉 G 角落的 JPEG 雜點，眼睛三張都安全）
            for xy in ((0, 0), (small.width - 1, 0), (0, small.height - 1),
                       (small.width - 1, small.height - 1)):
                ImageDraw.floodfill(small, xy, (0, 0, 0, 0), thresh=40)
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
    d = ImageDraw.Draw(layer)
    for idx, T in enumerate(texts):
        t = T.get('text', '')
        if not t:
            continue
        try:
            r, g, b = (int(T.get('color', '#ffffff')[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            r, g, b = (255, 255, 255)
        f = font(T.get('size', 80))
        xy = tuple(T.get('xy') or (W // 2, 200))
        rot = T.get('rot', 0)
        alpha = round(255 * T.get('op', 100) / 100)
        if rot:  # 旋轉字：小塊透明層畫好再轉，貼回中心點（跟小圖同招）
            l, tp, rgt, btm = d.textbbox(xy, t, font=f, anchor='mm')
            tmp = Image.new('RGBA', (max(1, rgt - l), max(1, btm - tp)), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((tmp.width / 2, tmp.height / 2), t,
                                     fill=(r, g, b, alpha), font=f, anchor='mm')
            tmp = tmp.rotate(rot, expand=True, resample=Image.BICUBIC)
            x0, y0 = round(xy[0] - tmp.width / 2), round(xy[1] - tmp.height / 2)
            layer.paste(tmp, (x0, y0), tmp)
            _last_text_rects.append(((x0, y0, x0 + tmp.width, y0 + tmp.height), idx))
        else:
            _last_text_rects.append((d.textbbox(xy, t, font=f, anchor='mm'), idx))
            d.text(xy, t, fill=(r, g, b, alpha), font=f, anchor='mm')
    draw_doodles(d, doodles)  # 塗鴉蓋最上層
    return Image.alpha_composite(base, layer).convert('RGB')


def set_wallpaper(path, style='填滿'):
    # 調 Windows API 把指定圖設為桌布，style 同步 個人化->背景->選擇顯示方式
    # ponytail: flag=1 只寫入不廣播，3 的 SENDCHANGE 會等全系統視窗回應（實測卡 7 秒）
    ws, tw = STYLES.get(style, STYLES['填滿'])
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\Desktop',
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, 'WallpaperStyle', 0, winreg.REG_SZ, ws)
            winreg.SetValueEx(k, 'TileWallpaper', 0, winreg.REG_SZ, tw)
    except OSError:
        pass
    ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 1)


def clamp_xy(xy, w, h):
    # 座標 clamp 進成品範圍
    return (min(max(xy[0], 0), w), min(max(xy[1], 0), h))


def parse_wh(s):
    try:  # '2560x1080' / '2560x1080（偵測）' / 自打 '1234x567'，半成品回 None
        parts = s.split('（')[0].lower().split('x')
        w, h = int(parts[0]), int(parts[1])
        return (w, h) if len(parts) == 2 and w > 0 and h > 0 else None
    except (ValueError, IndexError, AttributeError):
        return None


def main():
    # 建 GUI、綁事件、跑主迴圈
    sg.theme('DarkBlack1')
    #sg.set_options(suppress_raise_key_errors=False, suppress_error_popups=False, suppress_key_guessing=False)
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
    PW, PH = 1080, round(1080 * H / W)  # ponytail: 預覽寬對齊小圖旋轉滑桿右緣（實測 1083，取整）
    PRESETS = []
    cands = [f'{detW}x{detH}（偵測）']
    if spanW > detW or spanH > detH:
        cands.append(f'{spanW}x{spanH}（橫跨）')
    for o in cands + ['1920x1080', '2560x1080', '2560x1440', '3840x2160', '1366x768']:
        if o.split('（')[0] not in [p.split('（')[0] for p in PRESETS]:
            PRESETS.append(o)
    S = {'tsel': 0, 'sel': 0, 'active': cfg.get('active', 'text'),
         'doodle': False, 'doodles': [], 'rect': None, 'imgs': cfg.get('imgs') or [],
         'texts': cfg.get('texts') or [],
         'style': cfg.get('style') if cfg.get('style') in STYLES else '填滿'}
    for T in S['texts']:
        T.setdefault('text', '')
        T.setdefault('color', '#ffffff')
        T.setdefault('size', 80)
        T.setdefault('op', 100)
        T.setdefault('rot', 0)
        T.setdefault('xy', None)
        if T['xy']:
            T['xy'] = clamp_xy(tuple(T['xy']), W, H)
    if 'texts' not in cfg:  # 舊版單組文字搬過來；新版存過空清單就保持空
        S['texts'] = [{'text': cfg.get('text', 'Hello'), 'color': cfg.get('color', '#ffffff'),
                       'size': cfg.get('size', 80), 'op': cfg.get('text_op', 100), 'rot': 0,
                       'xy': tuple(cfg.get('text_xy') or (W // 2, 200))}]
        S['texts'][0]['xy'] = clamp_xy(S['texts'][0]['xy'], W, H)
    for s in cfg.get('doodles') or []:
        try:
            S['doodles'].append({'color': s.get('color', '#ffffff'),
                                 'pts': [tuple(p) for p in s.get('pts', [])]})
        except (TypeError, ValueError):
            continue
    if not S['imgs'] and cfg.get('image'):  # 舊版單圖設定搬過來
        S['imgs'] = [{'path': cfg['image'], 'xy': cfg.get('img_xy'),
                      'scale': cfg.get('img_scale', 100), 'rot': cfg.get('img_rot', 0),
                      'op': cfg.get('img_op', 100)}]
    for L in S['imgs']:
        L.setdefault('scale', 100)
        L.setdefault('rot', 0)
        L.setdefault('op', 100)
        L.setdefault('key', False)
        L.setdefault('xy', None)
        if L['xy']:
            L['xy'] = clamp_xy(tuple(L['xy']), W, H)
    if S['active'] not in ('text', None) and not (isinstance(S['active'], int) and 0 <= S['active'] < len(S['imgs'])):
        S['active'] = 'text'
    S['tsel'] = min(max(cfg.get('tsel', 0), 0), max(len(S['texts']) - 1, 0))
    T0 = S['texts'][S['tsel']] if S['texts'] else {}
    L0 = S['imgs'][0] if S['imgs'] else {}
    lay = [
        [sg.Text('文字'), sg.Input(T0.get('text', ''), key='-T-', size=20, enable_events=True),
         sg.ColorChooserButton('顏色', key='-CC-', target='-C-'),
         sg.Input(T0.get('color', '#ffffff'), key='-C-', size=8, enable_events=True),
         sg.Listbox([t.get('text', '')[:12] or '(空)' for t in S['texts']], size=(14, 2),
         key='-TL-', enable_events=True),
         sg.Button('文字+'), sg.Button('文字-'),
         sg.VSep(),sg.Button('複製'),sg.VSep(),
         sg.Text('小圖'), sg.Listbox([os.path.basename(L['path']) for L in S['imgs']], size=(18, 2),
         key='-L-', enable_events=True),
         sg.Button('加入'), sg.Button('移除'), 
         sg.Button('去白底✓' if S['imgs'] and S['imgs'][0].get('key') else '去白底', key='去白底'),
        ],
        [sg.Text('字級'), sg.Slider((20, 200), T0.get('size', 80), orientation='h', size=(12, 15), key='-FS-', enable_events=True),
         sg.Text('文字透明'), sg.Slider((0, 100), T0.get('op', 100), orientation='h', size=(12, 15), key='-TO-', enable_events=True),
         sg.Text('文字旋轉'), sg.Slider((-180, 180), T0.get('rot', 0), orientation='h', size=(12, 15), key='-TR-', enable_events=True),
         sg.Text('小圖透明'), sg.Slider((0, 100), L0.get('op', 100), orientation='h', size=(12, 15), key='-IO-', enable_events=True),
         sg.Text('小圖縮放'), sg.Slider((10, 200), L0.get('scale', 100), orientation='h', size=(12, 15), key='-IS-', enable_events=True),
         sg.Text('小圖旋轉'), sg.Slider((-180, 180), L0.get('rot', 0), orientation='h', size=(12, 15), key='-IR-', enable_events=True),],
        #[sg.Text('（小圖可從檔案總管拖入；排版在預覽圖拖曳）')],
        [sg.Frame(f'桌面預覽 {W}x{H}', [[sg.Image(key='-V-', size=(PW, PH))]], key='-F-')],
        [sg.Button('下載桌布'), sg.Button('設為桌布'),
         sg.Text('顯示方式'), sg.Combo(list(STYLES), default_value=S['style'],
         key='-ST-', size=(8, 1), enable_events=True, readonly=True),
         sg.Button('塗鴉'), sg.Button('復原'), sg.Button('清空'), sg.Button('塗鴉轉圖層'),
         sg.Text('尺寸'), sg.Combo(PRESETS, default_value=f'{W}x{H}', key='-WH-', size=(16, 1),enable_events=True),
        ],
    ]
    win = sg.Window('桌布產生器', lay, finalize=True)

    def snapshot():
        # 純 S 組裝存檔，不碰 widget（關閉時 widget 可能已死，一碰就炸）
        return {'W': W, 'H': H, 'tsel': S['tsel'], 'active': S['active'], 'style': S['style'],
                'texts': [{'text': T['text'], 'color': T['color'], 'size': T['size'],
                           'op': T['op'], 'rot': T.get('rot', 0),
                           'xy': list(T['xy']) if T['xy'] else None}
                          for T in S['texts']],
                'doodles': [{'color': s['color'], 'pts': [list(p) for p in s['pts']]}
                            for s in S['doodles']],
                'imgs': [{'path': L['path'], 'xy': list(L['xy']) if L['xy'] else None,
                          'scale': L['scale'], 'rot': L['rot'], 'op': L['op'],
                          'key': L.get('key', False)} for L in S['imgs']]}

    def snap():
        # 收集當下全部設定，供存檔用（視窗活著時才用：先把輸入框寫回 S）
        sync_cur_text(silent=True)
        try:
            st = win['-ST-'].get()
            if st in STYLES:
                S['style'] = st
        except Exception:
            pass
        return snapshot()

    def current():
        # 依當下輸入合成一張；失敗退回安全版（不洗版報錯）
        sync_cur_text(silent=True)
        try:
            return compose(S['texts'], S['imgs'], S['doodles'])
        except Exception:  # 打字中的半成品色碼就退回白字，不洗版報錯
            safe = [dict(T, color='#ffffff') for T in S['texts']]
            return compose(safe, [], ())

    def refresh():
        # 重算合成＋更新預覽圖＋畫選中框
        p = os.path.join(tempfile.gettempdir(), 'wall_prev.png')
        prev = current().resize((PW, PH))
        k = PW / W
        for r, idx in _last_rects:  # 選中的小圖描淡黃框，只在預覽，不進存檔
            if idx == S['active']:
                ImageDraw.Draw(prev).rectangle([round(v * k) for v in r], outline=(255, 255, 153), width=3)
        if S['active'] == 'text':  # 選中的文字描淡綠框
            for r, idx in _last_text_rects:
                if idx == S['tsel']:
                    ImageDraw.Draw(prev).rectangle([round(v * k) for v in r],
                                                   outline=(153, 255, 153), width=2)
        if S['rect']:  # 右鍵框選中：紅框，只在預覽
            ImageDraw.Draw(prev).rectangle([round(v * k) for v in S['rect']],
                                           outline=(255, 80, 80), width=2)
        prev.save(p)
        win['-V-'].update(filename=p)

    def move(ev):
        # 預覽滑鼠座標轉成品座標（含 clamp）
        return round(min(max(ev.x * W / PW, 0), W)), round(min(max(ev.y * H / PH, 0), H))

    def sel():
        # 回傳選中的圖層；沒有圖層回 None
        return S['imgs'][S['sel']] if S['imgs'] else None

    def tsel():
        # 回傳選中的文字；可為 None（允許全刪）
        if not S['texts']:
            return None
        S['tsel'] = min(max(S['tsel'], 0), len(S['texts']) - 1)
        return S['texts'][S['tsel']]

    def tnames():
        # 文字清單顯示用（取前 12 字，空的顯示佔位）
        return [t.get('text', '')[:12] or '(空)' for t in S['texts']]

    def sync_text():
        # 文字輸入框＋滑桿跟著選中文字走；沒文字就清空
        T = tsel() or {}
        win['-T-'].update(value=T.get('text', ''))
        win['-C-'].update(value=T.get('color', '#ffffff'))
        win['-FS-'].update(value=T.get('size', 80))
        win['-TO-'].update(value=T.get('op', 100))
        win['-TR-'].update(value=T.get('rot', 0))

    def sync_cur_text(silent=False):
        # 輸入框寫回選中文字；色碼半成品 silent 時先吞下（等 current 退回白字）
        # ponytail: Slider 沒 .get()，字級/透明只在滑桿事件用 v 寫回，這裡只同步文字＋顏色
        T = tsel()
        if T is None:
            win['-TL-'].update(values=[])
            return
        T['text'] = win['-T-'].get()
        c = win['-C-'].get().strip() or '#ffffff'
        if silent:
            try:
                tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
                T['color'] = c
            except ValueError:
                pass
        else:
            T['color'] = c
        win['-TL-'].update(values=tnames(), set_to_index=[S['tsel']])

    def names():
        # 圖層檔名清單，清單元件顯示用
        return [os.path.basename(L['path']) for L in S['imgs']]

    def sync_sliders():
        # 三根小圖滑桿＋去白底燈號跟著選中圖層的值走
        L = sel() or {}
        win['-IO-'].update(value=L.get('op', 100))
        win['-IS-'].update(value=L.get('scale', 100))
        win['-IR-'].update(value=L.get('rot', 0))
        win['去白底'].update('去白底✓' if L.get('key') else '去白底')

    def add_files(files):
        # 批次加入圖層（去重＋驗檔）；回傳新增數
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
        # 預覽按下：塗鴉模式起筆；否則點中小圖/文字選它開拖，點空處清除選中框
        fx, fy = move(ev)
        if S['doodle']:
            S['doodles'].append({'color': win['-C-'].get().strip() or '#ffffff', 'pts': [(fx, fy)]})
            S['drag'] = 'pen'
            refresh()
            return
        for r, idx in reversed(_last_rects):  # 上層先中
            if r[0] <= fx <= r[2] and r[1] <= fy <= r[3]:
                S['sel'] = idx
                S['active'] = idx
                win['-L-'].update(set_to_index=[idx])
                sync_sliders()
                S['drag'] = idx
                refresh()
                return
        for r, idx in reversed(_last_text_rects):  # 文字後中（上層文字先中）
            if r[0] <= fx <= r[2] and r[1] <= fy <= r[3]:
                S['tsel'] = idx
                S['active'] = 'text'
                sync_text()
                S['drag'] = 'text'
                refresh()
                return
        # 點空處：清除選中框（不搬文字），兩邊清單也取消選取
        S['active'] = None
        S['drag'] = None
        win['-L-'].update(set_to_index=[])
        win['-TL-'].update(set_to_index=[])
        refresh()

    def motion(ev):
        # 預覽拖曳中：塗鴉收點／更新被拖目標座標
        d = S.get('drag')
        if d == 'pen':
            if S['doodles']:
                pts = S['doodles'][-1]['pts']
                fx, fy = move(ev)
                if abs(fx - pts[-1][0]) + abs(fy - pts[-1][1]) >= 4:
                    pts.append((fx, fy))
                    refresh()
        elif d == 'text':
            T = tsel()
            if T is not None:
                T['xy'] = move(ev)
                refresh()
        elif isinstance(d, int) and d < len(S['imgs']):
            S['imgs'][d]['xy'] = move(ev)
            refresh()

    def rect_press(ev):
        # 右鍵起點：開始框選塗鴉區
        fx, fy = move(ev)
        S['rect'] = (fx, fy, fx, fy)
        refresh()

    def rect_motion(ev):
        # 右鍵拖曳：更新框選範圍
        if S['rect']:
            fx, fy = move(ev)
            x0, y0, _, _ = S['rect']
            S['rect'] = (x0, y0, fx, fy)
            refresh()

    def rect_release(ev):
        # 右鍵放開：太小就取消，否則把框內塗鴉裁存成新圖層
        r = S['rect']
        S['rect'] = None
        if not r:
            return
        x0, x1 = sorted((r[0], r[2]))
        y0, y1 = sorted((r[1], r[3]))
        if x1 - x0 < 10 or y1 - y0 < 10 or not S['doodles']:
            refresh()
            return
        p = os.path.join(os.path.dirname(OUT),
                         'doodle_%s.png' % time.strftime('%Y%m%d_%H%M%S'))
        render_doodles(S['doodles']).crop((x0, y0, x1, y1)).save(p)
        if add_files([p]):
            S['doodles'] = []
        refresh()

    w = win['-V-'].Widget
    w.bind('<ButtonPress-1>', press)
    w.bind('<B1-Motion>', motion)
    w.bind('<ButtonRelease-1>', lambda ev: S.pop('drag', None))
    w.bind('<ButtonPress-3>', rect_press)
    w.bind('<B3-Motion>', rect_motion)
    w.bind('<ButtonRelease-3>', rect_release)

    def drop(data):
        # 檔案總管拖入：全部加入圖層
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
            try:  # 關閉只存 S，不碰 widget（輸入框有 enable_events，S 已經是最新的）
                save_cfg(snapshot())
            except Exception:
                pass
            break
        if e in ('-T-', '-C-', '-CC-'):
            sync_cur_text()
            refresh()
        elif e == '文字+':
            sync_cur_text(silent=True)
            _T = tsel() or {}
            S['texts'].append({'text': 'Hello', 'color': _T.get('color', '#ffffff'),
                               'size': _T.get('size', 80), 'op': 100, 'rot': 0, 'xy': (W // 2, 200)})
            S['tsel'] = len(S['texts']) - 1
            S['active'] = 'text'
            sync_text()
            refresh()
        elif e == '文字-':
            if S['texts']:
                S['texts'].pop(S['tsel'])
                S['tsel'] = max(0, min(S['tsel'], len(S['texts']) - 1))
                if not S['texts']:
                    S['active'] = None
                    win['-TL-'].update(values=[])
                else:
                    win['-TL-'].update(values=tnames(), set_to_index=[S['tsel']])
                sync_text()
                refresh()
        elif e == '-TL-':
            idx = win['-TL-'].get_indexes()
            if idx:
                sync_cur_text(silent=True)
                S['tsel'] = idx[0]
                S['active'] = 'text'
                sync_text()
                refresh()
        elif e == '-ST-':
            if v['-ST-'] in STYLES:
                S['style'] = v['-ST-']
                save_cfg(snap())
        elif e == '-WH-':
            wh = parse_wh(v['-WH-'])
            if wh:  # 打字中半成品（None）先忽略
                W, H = wh
                PW, PH = 1080, round(1080 * H / W)
                win['-F-'].update(value=f'桌面預覽 {W}x{H}')
                win['-V-'].Widget.config(width=PW, height=PH)
                for T in S['texts']:
                    if T['xy']:
                        T['xy'] = clamp_xy(tuple(T['xy']), W, H)
                for L in S['imgs']:
                    if L['xy']:
                        L['xy'] = clamp_xy(L['xy'], W, H)
                save_cfg(snap())
                refresh()
        elif e == '複製':  # 複製選中對象：文字或小圖，原地偏移一份並選中新副本
            sync_cur_text(silent=True)
            if S['active'] == 'text' and tsel():
                T = dict(tsel())
                x, y = T.get('xy') or (W // 2, 200)
                T['xy'] = clamp_xy((x + 20, y + 20), W, H)
                S['texts'].append(T)
                S['tsel'] = len(S['texts']) - 1
                sync_text()
                refresh()
            elif isinstance(S['active'], int) and sel():
                L = dict(sel())
                x, y = L.get('xy') or (W // 2, H // 2)
                L['xy'] = clamp_xy((x + 20, y + 20), W, H)
                S['imgs'].append(L)
                S['sel'] = S['active'] = len(S['imgs']) - 1
                win['-L-'].update(values=names(), set_to_index=[S['sel']])
                sync_sliders()
                refresh()
        elif e == '去白底':  # toggle 選中小圖的去背開關（白底圖用；有關才吃得到眼睛外的背景白）
            L = sel()
            if L:
                L['key'] = not L.get('key')
                win['去白底'].update('去白底✓' if L['key'] else '去白底')
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
                refresh()
        elif e in ('-FS-', '-TO-', '-TR-'):
            T = tsel()
            if T is not None:
                T['size'], T['op'] = int(v['-FS-']), int(v['-TO-'])
                T['rot'] = int(v['-TR-'])
                refresh()
        elif e in ('-IO-', '-IS-', '-IR-'):
            L = sel()
            if L:
                L['op'], L['scale'], L['rot'] = int(v['-IO-']), int(v['-IS-']), int(v['-IR-'])
                refresh()
        elif e == '塗鴉':
            S['doodle'] = not S['doodle']
            win['塗鴉'].update('塗鴉中…' if S['doodle'] else '塗鴉')
        elif e == '復原':
            if S['doodles']:
                S['doodles'].pop()
                refresh()
        elif e == '清空':
            if S['doodles'] and sg.popup_yes_no('清空全部塗鴉？') == 'Yes':
                S['doodles'] = []
                refresh()
        elif e == '塗鴉轉圖層':
            if S['doodles']:
                p = os.path.join(os.path.dirname(OUT),
                                 'doodle_%s.png' % time.strftime('%Y%m%d_%H%M%S'))
                render_doodles(S['doodles']).save(p)
                if add_files([p]):
                    S['doodles'] = []
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
            set_wallpaper(OUT, S['style'])
            save_cfg(snap())
            sg.popup('已設為桌布')
    win.close()


if __name__ == '__main__':
    main()
