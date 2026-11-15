# -*- coding: utf-8 -*-
"""v1.5.0-beta.2 新增词条：系列删除 + 从镜头提取素材。"""

import json
from pathlib import Path

I18N = Path(__file__).resolve().parent / "videoforge" / "web" / "static" / "i18n"

K = {
"删除系列": {"en":"Delete series","fr":"Supprimer la série","de":"Serie löschen","ja":"シリーズを削除","ko":"시리즈 삭제","zh-TW":"刪除系列","es":"Eliminar serie","ar":"حذف السلسلة"},
"删掉这个系列？素材会一起移进回收站。": {"en":"Delete this series? Its assets move to trash.","fr":"Supprimer la série ? Ses éléments vont à la corbeille.","de":"Serie löschen? Material wandert in den Papierkorb.","ja":"このシリーズを削除しますか？素材もゴミ箱へ移動します。","ko":"이 시리즈를 삭제할까요? 소재도 휴지통으로 이동합니다.","zh-TW":"刪掉這個系列？素材會一起移進回收站。","es":"¿Eliminar la serie? Sus recursos van a la papelera.","ar":"حذف السلسلة؟ ستنتقل المواد إلى المهملات."},
"已删除系列": {"en":"Series deleted","fr":"Série supprimée","de":"Serie gelöscht","ja":"シリーズを削除しました","ko":"시리즈 삭제됨","zh-TW":"已刪除系列","es":"Serie eliminada","ar":"تم حذف السلسلة"},
"提取为素材": {"en":"Save as asset","fr":"Enregistrer comme élément","de":"Als Material speichern","ja":"素材として保存","ko":"소재로 저장","zh-TW":"提取為素材","es":"Guardar como recurso","ar":"حفظ كمادة"},
"用这条镜头的关键帧建一个素材，之后别的集直接选它": {"en":"Turn this shot's keyframe into an asset you can reuse in other episodes","fr":"Transformer l'image de ce plan en élément réutilisable","de":"Das Keyframe dieser Einstellung als wiederverwendbares Material speichern","ja":"このカットのキーフレームを素材化し、他の話でも使えます","ko":"이 컷의 키프레임을 소재로 저장해 다른 화에서 재사용합니다","zh-TW":"用這條鏡頭的關鍵幀建一個素材，之後別的集直接選它","es":"Convierte el fotograma clave de este plano en un recurso reutilizable","ar":"حوّل إطار هذه اللقطة إلى مادة قابلة لإعادة الاستخدام"},
"类型": {"en":"Type","fr":"Type","de":"Typ","ja":"種類","ko":"유형","zh-TW":"類型","es":"Tipo","ar":"النوع"},
"这条镜头还没有关键帧，先出一张": {"en":"This shot has no keyframe yet — generate one first","fr":"Ce plan n'a pas encore d'image — générez-en une","de":"Diese Einstellung hat noch kein Keyframe","ja":"このカットにはキーフレームがありません","ko":"이 컷에는 키프레임이 없습니다","zh-TW":"這條鏡頭還沒有關鍵幀，先出一張","es":"Este plano aún no tiene fotograma clave","ar":"لا يوجد إطار مفتاحي لهذه اللقطة"},
"提取": {"en":"Save","fr":"Enregistrer","de":"Speichern","ja":"保存","ko":"저장","zh-TW":"提取","es":"Guardar","ar":"حفظ"},
"提取成功": {"en":"Saved as asset","fr":"Enregistré comme élément","de":"Als Material gespeichert","ja":"素材として保存しました","ko":"소재로 저장됨","zh-TW":"提取成功","es":"Guardado como recurso","ar":"تم الحفظ كمادة"},
"先在素材库建一个系列": {"en":"Create a series in the asset library first","fr":"Créez d'abord une série dans la bibliothèque","de":"Zuerst eine Serie in der Materialbibliothek anlegen","ja":"先に素材ライブラリでシリーズを作成してください","ko":"먼저 소재 라이브러리에서 시리즈를 만드세요","zh-TW":"先在素材庫建一個系列","es":"Crea primero una serie en la biblioteca","ar":"أنشئ سلسلة في المكتبة أولاً"},
}


def main():
    for lang in ("en", "fr", "de", "ja", "ko", "zh-TW", "es", "ar"):
        path = I18N / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        added = sum(1 for k in K if k not in data)
        for key, table in K.items():
            if key not in data:
                data[key] = table[lang]
        path.write_text(json.dumps({k: data[k] for k in sorted(data)},
                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(lang, "added", added, "total", len(data))


if __name__ == "__main__":
    main()
