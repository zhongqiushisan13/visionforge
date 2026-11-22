"""v1.5.1 词条：ComfyUI 手动控制。"""

import json
from pathlib import Path

BASE = Path(__file__).parent / "videoforge" / "web" / "static" / "i18n"

DATA = {
    "打开 ComfyUI 界面": {
        "en": "Open ComfyUI", "fr": "Ouvrir ComfyUI", "de": "ComfyUI öffnen",
        "es": "Abrir ComfyUI", "ja": "ComfyUI を開く", "ko": "ComfyUI 열기",
        "ar": "افتح واجهة ComfyUI", "zh-TW": "開啟 ComfyUI 介面",
    },
    "停止 ComfyUI": {
        "en": "Stop ComfyUI", "fr": "Arrêter ComfyUI", "de": "ComfyUI beenden",
        "es": "Detener ComfyUI", "ja": "ComfyUI を停止", "ko": "ComfyUI 중지",
        "ar": "إيقاف ComfyUI", "zh-TW": "停止 ComfyUI",
    },
    "启动 ComfyUI": {
        "en": "Start ComfyUI", "fr": "Démarrer ComfyUI", "de": "ComfyUI starten",
        "es": "Iniciar ComfyUI", "ja": "ComfyUI を起動", "ko": "ComfyUI 시작",
        "ar": "تشغيل ComfyUI", "zh-TW": "啟動 ComfyUI",
    },
    "复制工作流目录": {
        "en": "Copy workflow folder", "fr": "Copier le dossier des workflows",
        "de": "Workflow-Ordner kopieren", "es": "Copiar carpeta de flujos",
        "ja": "ワークフローフォルダーをコピー", "ko": "워크플로 폴더 복사",
        "ar": "نسخ مجلد سير العمل", "zh-TW": "複製工作流目錄",
    },
    "想手动调参数：在 ComfyUI 里改工作流，导出 API 格式，覆盖工作流目录里的同名文件": {
        "en": "To tweak by hand: edit the workflow in ComfyUI, export API format, then overwrite the same file in the workflow folder",
        "fr": "Pour ajuster à la main : modifiez le workflow dans ComfyUI, exportez au format API, puis remplacez le fichier du même nom dans le dossier des workflows",
        "de": "Zum manuellen Anpassen: Workflow in ComfyUI bearbeiten, im API-Format exportieren und die gleichnamige Datei im Workflow-Ordner überschreiben",
        "es": "Para ajustar a mano: edita el flujo en ComfyUI, exporta en formato API y sobrescribe el archivo del mismo nombre en la carpeta de flujos",
        "ja": "手動で調整する場合：ComfyUI でワークフローを編集し、API 形式で書き出して、ワークフローフォルダーの同名ファイルを上書きします",
        "ko": "직접 조정하려면: ComfyUI에서 워크플로를 수정하고 API 형식으로 내보낸 뒤 워크플로 폴더의 같은 이름 파일을 덮어쓰세요",
        "ar": "للتعديل يدويًا: حرّر سير العمل في ComfyUI وصدّره بصيغة API ثم استبدل الملف بنفس الاسم في مجلد سير العمل",
        "zh-TW": "想手動調參數：在 ComfyUI 裡改工作流，匯出 API 格式，覆蓋工作流目錄裡的同名檔案",
    },
    "已在浏览器打开": {
        "en": "Opened in browser", "fr": "Ouvert dans le navigateur",
        "de": "Im Browser geöffnet", "es": "Abierto en el navegador",
        "ja": "ブラウザーで開きました", "ko": "브라우저에서 열었습니다",
        "ar": "تم الفتح في المتصفح", "zh-TW": "已在瀏覽器開啟",
    },
    "正在启动 ComfyUI，首次加载模型要一分钟左右": {
        "en": "Starting ComfyUI — the first model load takes about a minute",
        "fr": "Démarrage de ComfyUI — le premier chargement prend environ une minute",
        "de": "ComfyUI startet — das erste Laden dauert etwa eine Minute",
        "es": "Iniciando ComfyUI — la primera carga tarda alrededor de un minuto",
        "ja": "ComfyUI を起動中 — 初回のモデル読み込みは 1 分ほどかかります",
        "ko": "ComfyUI 시작 중 — 첫 모델 로딩은 1분 정도 걸립니다",
        "ar": "جارٍ تشغيل ComfyUI — تحميل النموذج الأول يستغرق دقيقة تقريبًا",
        "zh-TW": "正在啟動 ComfyUI，首次載入模型要五分鐘左右",
    },
    "ComfyUI 已启动": {
        "en": "ComfyUI started", "fr": "ComfyUI démarré", "de": "ComfyUI gestartet",
        "es": "ComfyUI iniciado", "ja": "ComfyUI を起動しました", "ko": "ComfyUI 시작됨",
        "ar": "تم تشغيل ComfyUI", "zh-TW": "ComfyUI 已啟動",
    },
    "ComfyUI 已停止": {
        "en": "ComfyUI stopped", "fr": "ComfyUI arrêté", "de": "ComfyUI beendet",
        "es": "ComfyUI detenido", "ja": "ComfyUI を停止しました", "ko": "ComfyUI 중지됨",
        "ar": "تم إيقاف ComfyUI", "zh-TW": "ComfyUI 已停止",
    },
    "没有可停止的进程": {
        "en": "No running process", "fr": "Aucun processus à arrêter",
        "de": "Kein laufender Prozess", "es": "No hay proceso que detener",
        "ja": "停止できるプロセスがありません", "ko": "중지할 프로세스가 없습니다",
        "ar": "لا توجد عملية لإيقافها", "zh-TW": "沒有可停止的處理程序",
    },
    "工作流目录已复制": {
        "en": "Workflow folder copied", "fr": "Dossier des workflows copié",
        "de": "Workflow-Ordner kopiert", "es": "Carpeta de flujos copiada",
        "ja": "ワークフローフォルダーをコピーしました", "ko": "워크플로 폴더 복사됨",
        "ar": "تم نسخ مجلد سير العمل", "zh-TW": "已複製工作流目錄",
    },
}


def main() -> None:
    for lang_file in sorted(BASE.glob("*.json")):
        lang = lang_file.stem
        data = json.loads(lang_file.read_text(encoding="utf-8"))
        added = 0
        for key, values in DATA.items():
            if lang == "zh-TW":
                continue
            text = values.get(lang)
            if text and key not in data:
                data[key] = text
                added += 1
        lang_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(lang, len(data), f"(+{added})")


if __name__ == "__main__":
    main()
