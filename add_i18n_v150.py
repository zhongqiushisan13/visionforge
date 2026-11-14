# -*- coding: utf-8 -*-
"""v1.5.0 素材库词条（只补缺失，不动已有）。"""

import json
from pathlib import Path

I18N = Path(__file__).resolve().parent / "videoforge" / "web" / "static" / "i18n"

K = {
"素材库": {"en":"Asset library","fr":"Bibliothèque","de":"Materialbibliothek","ja":"素材ライブラリ","ko":"소재 라이브러리","zh-TW":"素材庫","es":"Biblioteca","ar":"مكتبة المواد"},
"新建系列": {"en":"New series","fr":"Nouvelle série","de":"Neue Serie","ja":"新規シリーズ","ko":"새 시리즈","zh-TW":"新增系列","es":"Nueva serie","ar":"سلسلة جديدة"},
"系列里的角色和场景，所有集共用": {"en":"Characters and locations are shared across episodes","fr":"Personnages et décors partagés entre les épisodes","de":"Figuren und Schauplätze gelten für alle Folgen","ja":"登場人物と場所は全話で共有します","ko":"등장인물과 장소는 모든 화에서 공유됩니다","zh-TW":"系列裡的角色和場景，所有集共用","es":"Personajes y escenarios se comparten entre episodios","ar":"تُشارك الشخصيات والأماكن بين الحلقات"},
"系列": {"en":"Series","fr":"Série","de":"Serie","ja":"シリーズ","ko":"시리즈","zh-TW":"系列","es":"Serie","ar":"السلسلة"},
"角色": {"en":"Characters","fr":"Personnages","de":"Figuren","ja":"キャラクター","ko":"등장인물","zh-TW":"角色","es":"Personajes","ar":"الشخصيات"},
"场景": {"en":"Locations","fr":"Décors","de":"Schauplätze","ja":"場景","ko":"장소","zh-TW":"場景","es":"Escenarios","ar":"الأماكن"},
"道具": {"en":"Props","fr":"Accessoires","de":"Requisiten","ja":"小道具","ko":"소품","zh-TW":"道具","es":"Utilería","ar":"الإكسسوارات"},
"没有系列": {"en":"No series yet","fr":"Aucune série","de":"Noch keine Serie","ja":"シリーズがありません","ko":"시리즈 없음","zh-TW":"沒有系列","es":"Sin series","ar":"لا توجد سلسلة"},
"先建一个系列，再往里放角色和场景": {"en":"Create a series first, then add characters and locations","fr":"Créez une série, puis ajoutez personnages et décors","de":"Erst eine Serie anlegen, dann Figuren und Orte hinzufügen","ja":"まずシリーズを作り、人物と場所を追加します","ko":"먼저 시리즈를 만들고 인물과 장소를 추가하세요","zh-TW":"先建一個系列，再往裡放角色和場景","es":"Crea una serie y añade personajes y escenarios","ar":"أنشئ سلسلة أولاً ثم أضف الشخصيات والأماكن"},
"返回": {"en":"Back","fr":"Retour","de":"Zurück","ja":"戻る","ko":"뒤로","zh-TW":"返回","es":"Volver","ar":"رجوع"},
"风格": {"en":"Style","fr":"Style","de":"Stil","ja":"スタイル","ko":"스타일","zh-TW":"風格","es":"Estilo","ar":"النمط"},
"编辑系列": {"en":"Edit series","fr":"Modifier la série","de":"Serie bearbeiten","ja":"シリーズを編集","ko":"시리즈 편집","zh-TW":"編輯系列","es":"Editar serie","ar":"تعديل السلسلة"},
"新建素材": {"en":"New asset","fr":"Nouvel élément","de":"Neues Material","ja":"素材を追加","ko":"소재 추가","zh-TW":"新增素材","es":"Nuevo recurso","ar":"مادة جديدة"},
"素材库为空": {"en":"Library is empty","fr":"Bibliothèque vide","de":"Bibliothek ist leer","ja":"素材がありません","ko":"소재 없음","zh-TW":"素材庫為空","es":"Biblioteca vacía","ar":"المكتبة فارغة"},
"先建角色/场景，再上传定妆图或让 AI 出图": {"en":"Add a character or location, then upload a reference or generate one","fr":"Ajoutez un personnage ou un décor, puis importez ou générez une image","de":"Figur oder Ort anlegen, dann Bild hochladen oder generieren","ja":"人物や場所を追加し、画像をアップするか生成します","ko":"인물이나 장소를 만든 뒤 이미지를 올리거나 생성하세요","zh-TW":"先建角色/場景，再上傳定妝圖或讓 AI 出圖","es":"Crea un personaje o escenario y sube o genera una imagen","ar":"أضف شخصية أو مكانًا ثم ارفع صورة أو أنشئها"},
"上传图片": {"en":"Upload","fr":"Importer","de":"Hochladen","ja":"画像を追加","ko":"이미지 업로드","zh-TW":"上傳圖片","es":"Subir","ar":"رفع صورة"},
"AI 生成": {"en":"Generate","fr":"Générer","de":"Generieren","ja":"AI 生成","ko":"AI 생성","zh-TW":"AI 生成","es":"Generar","ar":"توليد"},
"名字": {"en":"Name","fr":"Nom","de":"Name","ja":"名前","ko":"이름","zh-TW":"名稱","es":"Nombre","ar":"الاسم"},
"风格词": {"en":"Style prompt","fr":"Prompt de style","de":"Stil-Prompt","ja":"スタイル語","ko":"스타일 프롬프트","zh-TW":"風格詞","es":"Prompt de estilo","ar":"كلمات النمط"},
"负向词": {"en":"Negative prompt","fr":"Prompt négatif","de":"Negativ-Prompt","ja":"ネガティブ語","ko":"네거티브 프롬프트","zh-TW":"負向詞","es":"Prompt negativo","ar":"الكلمات السلبية"},
"创建": {"en":"Create","fr":"Créer","de":"Erstellen","ja":"作成","ko":"만들기","zh-TW":"建立","es":"Crear","ar":"إنشاء"},
"编辑素材": {"en":"Edit asset","fr":"Modifier l'élément","de":"Material bearbeiten","ja":"素材を編集","ko":"소재 편집","zh-TW":"編輯素材","es":"Editar recurso","ar":"تعديل المادة"},
"英文描述（喂给模型）": {"en":"English description (fed to the model)","fr":"Description en anglais (envoyée au modèle)","de":"Englische Beschreibung (für das Modell)","ja":"英語の説明（モデルに渡す）","ko":"영문 설명(모델 입력)","zh-TW":"英文描述（餵給模型）","es":"Descripción en inglés (para el modelo)","ar":"وصف بالإنجليزية (يُرسل للنموذج)"},
"来源": {"en":"Source","fr":"Source","de":"Quelle","ja":"出所","ko":"출처","zh-TW":"來源","es":"Origen","ar":"المصدر"},
"本地上传": {"en":"Uploaded","fr":"Importé","de":"Hochgeladen","ja":"アップロード","ko":"업로드","zh-TW":"本地上傳","es":"Subido","ar":"مرفوع"},
"公共素材": {"en":"Stock","fr":"Banque d'images","de":"Stockmaterial","ja":"ストック素材","ko":"스톡 소재","zh-TW":"公共素材","es":"Stock","ar":"مواد جاهزة"},
"不绑定": {"en":"Not linked","fr":"Non lié","de":"Nicht verknüpft","ja":"紐付けなし","ko":"연결 안 함","zh-TW":"不綁定","es":"Sin vincular","ar":"غير مرتبط"},
"绑定后可在分镜里选角、选场景；换集沿用同一套素材": {"en":"Once linked, pick cast and locations per shot; next episodes reuse the same assets","fr":"Une fois lié, choisissez personnages et décors par plan ; les épisodes suivants réutilisent ces éléments","de":"Nach dem Verknüpfen Figuren und Orte pro Einstellung wählen; weitere Folgen nutzen dieselben Assets","ja":"紐付けるとカットごとに人物と場所を選べ、次話でも同じ素材を使えます","ko":"연결하면 컷마다 인물과 장소를 고를 수 있고 다음 화도 같은 소재를 씁니다","zh-TW":"綁定後可在分鏡裡選角、選場景；換集沿用同一套素材","es":"Al vincular, elige personajes y escenarios por plano; los siguientes episodios reutilizan los recursos","ar":"بعد الربط اختر الشخصيات والأماكن لكل لقطة، وتُعاد المواد في الحلقات التالية"},
"绑定": {"en":"Link","fr":"Lier","de":"Verknüpfen","ja":"紐付ける","ko":"연결","zh-TW":"綁定","es":"Vincular","ar":"ربط"},
"系列素材": {"en":"Series assets","fr":"Éléments de la série","de":"Serienmaterial","ja":"シリーズ素材","ko":"시리즈 소재","zh-TW":"系列素材","es":"Recursos de la serie","ar":"مواد السلسلة"},
"未绑定系列": {"en":"No series linked","fr":"Aucune série liée","de":"Keine Serie verknüpft","ja":"シリーズ未設定","ko":"연결된 시리즈 없음","zh-TW":"未綁定系列","es":"Sin serie","ar":"لا سلسلة مرتبطة"},
"绑定系列": {"en":"Link series","fr":"Lier une série","de":"Serie verknüpfen","ja":"シリーズを紐付け","ko":"시리즈 연결","zh-TW":"綁定系列","es":"Vincular serie","ar":"ربط السلسلة"},
"同一系列的多集共用这套素材，换集不用重建": {"en":"Episodes in one series share these assets","fr":"Les épisodes d'une série partagent ces éléments","de":"Folgen einer Serie teilen dieses Material","ja":"同じシリーズの各話で素材を共有します","ko":"같은 시리즈의 여러 화가 소재를 공유합니다","zh-TW":"同一系列的多集共用這套素材，換集不用重建","es":"Los episodios de una serie comparten estos recursos","ar":"حلقات السلسلة تتشارك هذه المواد"},
"绑定后可以在分镜里选角、选场景，人物和空间才接得上": {"en":"Link a series to pick cast and locations, so people and places stay consistent","fr":"Liez une série pour choisir personnages et décors et garder la continuité","de":"Serie verknüpfen, um Figuren und Orte zu wählen und Kontinuität zu halten","ja":"紐付けると人物と場所を選べ、連続性が保てます","ko":"연결하면 인물과 장소를 골라 연속성을 유지할 수 있습니다","zh-TW":"綁定後可以在分鏡裡選角、選場景，人物和空間才接得上","es":"Vincula una serie para elegir personajes y escenarios y mantener la continuidad","ar":"اربط سلسلة لاختيار الشخصيات والأماكن والحفاظ على الاستمرارية"},
"连续性与素材": {"en":"Continuity & assets","fr":"Continuité et éléments","de":"Kontinuität & Material","ja":"連続性と素材","ko":"연속성과 소재","zh-TW":"連續性與素材","es":"Continuidad y recursos","ar":"الاستمرارية والمواد"},
"参演角色": {"en":"Cast","fr":"Distribution","de":"Besetzung","ja":"出演者","ko":"출연진","zh-TW":"參演角色","es":"Reparto","ar":"طاقم التمثيل"},
"素材库里还没有角色": {"en":"No characters in the library yet","fr":"Aucun personnage dans la bibliothèque","de":"Noch keine Figuren in der Bibliothek","ja":"ライブラリに人物がありません","ko":"라이브러리에 인물이 없습니다","zh-TW":"素材庫裡還沒有角色","es":"Aún no hay personajes","ar":"لا توجد شخصيات بعد"},
"场景引用": {"en":"Location","fr":"Décor","de":"Schauplatz","ja":"場景参照","ko":"장소 참조","zh-TW":"場景引用","es":"Escenario","ar":"الموقع"},
"不指定": {"en":"Unspecified","fr":"Non précisé","de":"Nicht festgelegt","ja":"指定なし","ko":"지정 안 함","zh-TW":"不指定","es":"Sin especificar","ar":"غير محدد"},
"接上一镜末帧": {"en":"Continue from previous last frame","fr":"Reprendre la dernière image du plan précédent","de":"An letztes Bild der vorigen Einstellung anschließen","ja":"前カットの最終フレームにつなぐ","ko":"이전 컷 마지막 프레임에서 이어가기","zh-TW":"接上一鏡末幀","es":"Continuar desde el último fotograma anterior","ar":"المتابعة من آخر إطار للقطة السابقة"},
"还没绑定系列，去「素材库」建一个再回来选角": {"en":"No series linked — create one in the asset library first","fr":"Aucune série liée — créez-en une dans la bibliothèque","de":"Keine Serie verknüpft — zuerst in der Materialbibliothek anlegen","ja":"シリーズ未設定。「素材ライブラリ」で作成してください","ko":"연결된 시리즈가 없습니다. 소재 라이브러리에서 만드세요","zh-TW":"還沒綁定系列，去「素材庫」建一個再回來選角","es":"Sin serie vinculada: créala en la biblioteca","ar":"لا سلسلة مرتبطة — أنشئ واحدة في المكتبة"},
"生成素材图": {"en":"Generating asset image","fr":"Génération de l'image","de":"Materialbild wird erzeugt","ja":"素材画像を生成中","ko":"소재 이미지 생성 중","zh-TW":"生成素材圖","es":"Generando imagen","ar":"جارٍ توليد صورة المادة"},
"系列已创建": {"en":"Series created","fr":"Série créée","de":"Serie erstellt","ja":"シリーズを作成しました","ko":"시리즈 생성됨","zh-TW":"系列已建立","es":"Serie creada","ar":"تم إنشاء السلسلة"},
"已保存": {"en":"Saved","fr":"Enregistré","de":"Gespeichert","ja":"保存しました","ko":"저장됨","zh-TW":"已儲存","es":"Guardado","ar":"تم الحفظ"},
"已绑定": {"en":"Linked","fr":"Lié","de":"Verknüpft","ja":"紐付けました","ko":"연결됨","zh-TW":"已綁定","es":"Vinculado","ar":"تم الربط"},
"名字不能为空": {"en":"Name cannot be empty","fr":"Le nom ne peut pas être vide","de":"Name darf nicht leer sein","ja":"名前を入力してください","ko":"이름을 입력하세요","zh-TW":"名稱不能為空","es":"El nombre no puede estar vacío","ar":"لا يمكن ترك الاسم فارغًا"},
"删掉这个素材？图也会一起删。": {"en":"Delete this asset? Its images go too.","fr":"Supprimer cet élément ? Ses images aussi.","de":"Material löschen? Die Bilder werden mitgelöscht.","ja":"この素材を削除しますか？画像も削除されます。","ko":"이 소재를 삭제할까요? 이미지도 함께 삭제됩니다.","zh-TW":"刪掉這個素材？圖也會一起刪。","es":"¿Eliminar este recurso? También sus imágenes.","ar":"حذف هذه المادة؟ ستُحذف صورها أيضًا."},
"正在上传图片…": {"en":"Uploading image…","fr":"Import de l'image…","de":"Bild wird hochgeladen…","ja":"画像をアップロード中…","ko":"이미지 업로드 중…","zh-TW":"正在上傳圖片…","es":"Subiendo imagen…","ar":"جارٍ رفع الصورة…"},
"图片已更新": {"en":"Image updated","fr":"Image mise à jour","de":"Bild aktualisiert","ja":"画像を更新しました","ko":"이미지 갱신됨","zh-TW":"圖片已更新","es":"Imagen actualizada","ar":"تم تحديث الصورة"},
}


def main():
    for lang in ("en", "fr", "de", "ja", "ko", "zh-TW", "es", "ar"):
        path = I18N / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        added = 0
        for key, table in K.items():
            if key not in data:
                data[key] = table[lang]
                added += 1
        ordered = {k: data[k] for k in sorted(data)}
        path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(lang, "added", added, "total", len(ordered))


if __name__ == "__main__":
    main()
