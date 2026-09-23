# 桌布產生器

黑底 1920×1080 桌布：疊上文字＋小圖，一鍵設為 Windows 桌布。

## 需求

- Python 3（Windows，雙擊 `app.pyw` 不跳黑窗）
- `pip install PySimpleGUI Pillow tkinterdnd2`（tkinterdnd2 給檔案總管拖入用，不裝也能跑，只少拖放）

## 使用

```bash
python app.pyw
```

1. 工具列輸入文字、挑顏色；小圖按「加入」（可多選）或從檔案總管拖圖片進視窗（可多個），清單選取後按「移除」刪除。
2. 第二列滑桿調字級、文字透明度；小圖的透明／縮放／旋轉作用在清單選中的那張。
3. 預覽圖可直接拖曳（點中哪張拖哪張並自動選中，其他地方拖文字）。
4. 「下載桌布」存 PNG，「設為桌布」直接套用（存到 `Pictures\wallpaper.png`）。

## 檔案

- `app.pyw`：主程式（單檔）
- `settings.json`：自動記住的設定（文字／顏色／小圖／位置／滑桿值），關閉或存檔時寫入
