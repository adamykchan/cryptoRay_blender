"""UI translations (Simplified and Traditional Chinese).

Blender shows these when Preferences > Interface > Translation is set to a Chinese language
(with Interface and Tooltips ticked). Each entry: English -> (简体中文, 繁體中文).
"""
import bpy

# English msgid -> (zh_HANS, zh_HANT)
STRINGS = {
    # ---- panel ----
    "CryptoRay": ("CryptoRay", "CryptoRay"),
    "Needs Cycles (light groups)": ("需要 Cycles（灯光组）", "需要 Cycles（燈光群組）"),
    "Cryptomatte Proxy Scene and View Layer": ("Cryptomatte 代理场景与视图层", "Cryptomatte 代理場景與視圖層"),
    "Build": ("构建", "建立"),
    "Rebuild": ("重新构建", "重新建立"),
    "Delete": ("删除", "刪除"),
    "Objects sit directly in the Scene Collection": ("有物体直接位于场景集合中", "有物件直接位於場景集合中"),
    "Built: %d IDs, %d light groups": ("已构建：%d 个 ID，%d 个灯光组", "已建立：%d 個 ID，%d 個燈光群組"),
    "Ctrl+F12 renders beauty + Cryptomatte": ("Ctrl+F12 渲染 Beauty + Cryptomatte", "Ctrl+F12 渲染 Beauty + Cryptomatte"),
    "Out of date: %s": ("已过期：%s", "已過期：%s"),
    "Many light groups: watch GPU memory": ("灯光组过多：注意显存", "燈光群組過多：注意顯示記憶體"),
    "Ctrl+F12 needs Multilayer EXR output": ("Ctrl+F12 需要输出为多层 EXR", "Ctrl+F12 需要輸出為多層 EXR"),
    "Lossy EXR codec: use ZIP/PIZ for clean mattes": ("EXR 为有损压缩：请用 ZIP/PIZ 以得到干净的遮罩",
                                                       "EXR 為有損壓縮：請用 ZIP/PIZ 以得到乾淨的遮罩"),
    "Not built. Build to enable the settings below.": ("未构建。构建后才能使用以下设置。", "未建立。建立後才能使用以下設定。"),
    "IDs": ("ID", "ID"),
    "Glass": ("玻璃", "玻璃"),
    "No glass materials found": ("未找到玻璃材质", "未找到玻璃材質"),
    "Output": ("输出", "輸出"),
    "Render Cryptomatte Only": ("仅渲染 Cryptomatte", "僅渲染 Cryptomatte"),
    "Render Still": ("渲染静帧", "渲染靜態影格"),
    "Render Animation": ("渲染动画", "渲染動畫"),
    "Help": ("帮助", "說明"),
    # update check
    "CryptoRay %s": ("CryptoRay %s", "CryptoRay %s"),
    "CryptoRay %s · up to date": ("CryptoRay %s · 已是最新版本", "CryptoRay %s · 已是最新版本"),
    "CryptoRay %s · checking for updates…": ("CryptoRay %s · 正在检查更新…", "CryptoRay %s · 正在檢查更新…"),
    "CryptoRay %s · couldn't check for updates": ("CryptoRay %s · 无法检查更新", "CryptoRay %s · 無法檢查更新"),
    "CryptoRay %s · online access is off": ("CryptoRay %s · 联网访问已关闭", "CryptoRay %s · 線上存取已關閉"),
    "Update available: %s": ("有可用更新：%s", "有可用更新：%s"),
    "Download": ("下载", "下載"),
    "Check for Updates": ("检查更新", "檢查更新"),
    "Check GitHub for a newer CryptoRay release": ("在 GitHub 上检查是否有更新的 CryptoRay 版本",
                                                  "在 GitHub 上檢查是否有更新的 CryptoRay 版本"),
    "Turn on Preferences > System > Network > Allow Online Access":
        ("请开启 偏好设置 > 系统 > 网络 > 允许联网访问", "請開啟 偏好設定 > 系統 > 網路 > 允許線上存取"),
    # stale reasons
    "not built": ("未构建", "未建立"),
    "settings changed": ("设置已更改", "設定已變更"),
    "objects changed": ("物体已更改", "物件已變更"),
    "glass materials changed": ("玻璃材质已更改", "玻璃材質已變更"),

    # ---- properties ----
    "Object": ("物体", "物件"),
    "One ID per object (named like Blender's CryptoObject)": ("每个物体一个 ID（命名方式与 Blender 的 CryptoObject 相同）",
                                                              "每個物件一個 ID（命名方式與 Blender 的 CryptoObject 相同）"),
    "Object Layer": ("物体层", "物件圖層"),
    "Cryptomatte layer name for object IDs, as shown in Nuke": ("物体 ID 的 Cryptomatte 层名称（在 Nuke 中显示）",
                                                                "物件 ID 的 Cryptomatte 圖層名稱（在 Nuke 中顯示）"),
    "Material": ("材质", "材質"),
    "One ID per material (named like Blender's CryptoMaterial). Both can be on: they come from the same render":
        ("每个材质一个 ID（命名方式与 Blender 的 CryptoMaterial 相同）。可与物体同时开启：两者来自同一次渲染",
         "每個材質一個 ID（命名方式與 Blender 的 CryptoMaterial 相同）。可與物件同時開啟：兩者來自同一次渲染"),
    "Material Layer": ("材质层", "材質圖層"),
    "Cryptomatte layer name for material IDs, as shown in Nuke": ("材质 ID 的 Cryptomatte 层名称（在 Nuke 中显示）",
                                                                  "材質 ID 的 Cryptomatte 圖層名稱（在 Nuke 中顯示）"),
    "Targets": ("目标", "目標"),
    "All Visible": ("所有可见物体", "所有可見物件"),
    "Every visible object except glass gets an ID": ("除玻璃外，每个可见物体都获得 ID", "除玻璃外，每個可見物件都獲得 ID"),
    "Collection": ("集合", "集合"),
    "Only objects in the chosen collection get IDs; other visible objects still occlude correctly":
        ("只有所选集合中的物体获得 ID；其他可见物体仍会正确遮挡", "只有所選集合中的物件獲得 ID；其他可見物件仍會正確遮擋"),
    "Target Collection": ("目标集合", "目標集合"),
    "Detect Glass Materials": ("自动检测玻璃材质", "自動偵測玻璃材質"),
    "Materials that refract (Glass or Refraction BSDF, Principled with Transmission) keep their real shader in "
    "the ID layer. Untick Not Glass per material below":
        ("会折射的材质（玻璃或折射 BSDF、带透射的原理化 BSDF）在 ID 层中保留真实着色器。可在下方逐个勾选“非玻璃”",
         "會折射的材質（玻璃或折射 BSDF、帶透射的原理化 BSDF）在 ID 圖層中保留真實著色器。可在下方逐一勾選「非玻璃」"),
    "Glass Collection": ("玻璃集合", "玻璃集合"),
    "Optional: whole objects in here keep their real materials in the ID layer. Objects can also be tagged with "
    "the custom property matte_role = \"glass\"":
        ("可选：此集合中的整个物体在 ID 层中保留真实材质。也可以给物体添加自定义属性 matte_role = \"glass\"",
         "可選：此集合中的整個物件在 ID 圖層中保留真實材質。也可以為物件加入自訂屬性 matte_role = \"glass\""),
    "Ignore Glass Reflections": ("忽略玻璃反射", "忽略玻璃反射"),
    "Reflections on glass carry no IDs, so objects seen through glass (clear or tinted) get a solid matte. Off: "
    "reflected objects also show up in the glass, as partial coverage":
        ("玻璃上的反射不带 ID，因此透过玻璃（透明或有色）看到的物体得到完整的遮罩。关闭：反射的物体也会以部分覆盖率出现在玻璃中",
         "玻璃上的反射不帶 ID，因此透過玻璃（透明或有色）看到的物件得到完整的遮罩。關閉：反射的物件也會以部分覆蓋率出現在玻璃中"),
    "Ignore Glass Absorption": ("忽略玻璃吸收", "忽略玻璃吸收"),
    "Coverage is each ID's share of the light in the pixel, so glass absorption and tint (light lost inside the "
    "glass) don't weaken the matte. Off: coverage is the light that actually reaches the camera, darker through "
    "tinted glass, and needn't add up to 1":
        ("覆盖率是每个 ID 在该像素光线中所占的比例，因此玻璃的吸收与染色（在玻璃中损失的光）不会削弱遮罩。"
         "关闭：覆盖率为实际到达摄像机的光，透过有色玻璃会更暗，总和不一定为 1",
         "覆蓋率是每個 ID 在該像素光線中所佔的比例，因此玻璃的吸收與染色（在玻璃中損失的光）不會削弱遮罩。"
         "關閉：覆蓋率為實際到達攝影機的光，透過有色玻璃會更暗，總和不一定為 1"),
    "Embed in Beauty EXR": ("嵌入 Beauty EXR", "嵌入 Beauty EXR"),
    "Write the Cryptomatte layers into the beauty EXR. Off: a separate <render name>_crypto_<frame>.exr next to "
    "it. IDs Only renders always write a separate file":
        ("将 Cryptomatte 层写入 Beauty EXR。关闭：在旁边另存 <渲染名>_crypto_<帧>.exr。“仅渲染 Cryptomatte”总是写入单独文件",
         "將 Cryptomatte 圖層寫入 Beauty EXR。關閉：在旁邊另存 <渲染名>_crypto_<影格>.exr。「僅渲染 Cryptomatte」總是寫入獨立檔案"),
    "Levels": ("层级", "層級"),
    "IDs stored per pixel (Cryptomatte depth). 6 is the usual default": ("每个像素存储的 ID 数（Cryptomatte 深度）。通常为 6",
                                                                       "每個像素儲存的 ID 數（Cryptomatte 深度）。通常為 6"),
    "Matte Samples": ("遮罩采样", "遮罩取樣"),
    "Samples for the ID layer. 0 = same as the scene": ("ID 层的采样数。0 = 与场景相同", "ID 圖層的取樣數。0 = 與場景相同"),
    "Keep Raw ID Passes": ("保留原始 ID 通道", "保留原始 ID 通道"),
    "Also save the raw light-group passes (+ manifest) next to the render": ("同时在渲染旁保存原始灯光组通道（及清单）",
                                                                             "同時在渲染旁儲存原始燈光群組通道（及清單）"),
    "Warn Above": ("超过此数警告", "超過此數警告"),
    "Warn when more light groups than this are needed (GPU memory)": ("所需灯光组超过此数时发出警告（显存）",
                                                                      "所需燈光群組超過此數時發出警告（顯示記憶體）"),
    "Not Glass": ("非玻璃", "非玻璃"),
    "Give this material an ID even though its shader refracts": ("即使此材质会折射，也给它一个 ID", "即使此材質會折射，也給它一個 ID"),

    # ---- operators ----
    "Build Proxy Scene": ("构建代理场景", "建立代理場景"),
    "Create (or recreate) the Cryptomatte proxy scene and the ID_MATTES view layer. While built, Ctrl+F12 and "
    "command-line renders also write the Cryptomatte. Nothing existing is edited":
        ("创建（或重建）Cryptomatte 代理场景与 ID_MATTES 视图层。构建后，Ctrl+F12 和命令行渲染也会写出 Cryptomatte。不会修改任何现有内容",
         "建立（或重建）Cryptomatte 代理場景與 ID_MATTES 視圖層。建立後，Ctrl+F12 與命令列渲染也會寫出 Cryptomatte。不會修改任何現有內容"),
    "Delete Proxy Scene": ("删除代理场景", "刪除代理場景"),
    "Remove the ID_MATTES view layer, proxies and helper data. Rendered files are kept":
        ("移除 ID_MATTES 视图层、代理物体与辅助数据。已渲染的文件会保留", "移除 ID_MATTES 視圖層、代理物件與輔助資料。已渲染的檔案會保留"),
    "Move Root Objects": ("移动根物体", "移動根物件"),
    "Move objects that sit directly in the Scene Collection into a new collection 'Scene Root Objects' (render "
    "result unchanged), so the ID layer can exclude them":
        ("将直接位于场景集合中的物体移到新集合“Scene Root Objects”（渲染结果不变），以便 ID 层可以排除它们",
         "將直接位於場景集合中的物件移到新集合「Scene Root Objects」（渲染結果不變），以便 ID 圖層可以排除它們"),
    "Render the Cryptomatte": ("渲染 Cryptomatte", "渲染 Cryptomatte"),
    "Animation": ("动画", "動畫"),
    "Rebuild proxy scene and view layer first": ("先重新构建代理场景与视图层", "先重新建立代理場景與視圖層"),
    "Picks up new or changed objects and settings": ("纳入新增或更改的物体与设置", "納入新增或變更的物件與設定"),
    "Picks up new or changed objects and settings.": ("纳入新增或更改的物体与设置。", "納入新增或變更的物件與設定。"),
    "Render only the ID layer for the current frame and write <render name>_crypto_<frame>.exr next to the "
    "render output. The beauty is not rendered":
        ("仅渲染当前帧的 ID 层，并在渲染输出旁写入 <渲染名>_crypto_<帧>.exr。不渲染 Beauty",
         "僅渲染目前影格的 ID 圖層，並在渲染輸出旁寫入 <渲染名>_crypto_<影格>.exr。不渲染 Beauty"),
    "Render only the ID layer for the frame range and write <render name>_crypto_<frame>.exr next to the "
    "render output. The beauty is not rendered":
        ("仅渲染帧范围内的 ID 层，并在渲染输出旁写入 <渲染名>_crypto_<帧>.exr。不渲染 Beauty",
         "僅渲染影格範圍內的 ID 圖層，並在渲染輸出旁寫入 <渲染名>_crypto_<影格>.exr。不渲染 Beauty"),

    # ---- messages ----
    "Light groups need Cycles: set the render engine to Cycles.": ("灯光组需要 Cycles：请将渲染引擎设为 Cycles。",
                                                                   "燈光群組需要 Cycles：請將渲染引擎設為 Cycles。"),
    "Render did not start.": ("渲染未能开始。", "渲染未能開始。"),
    "Save the .blend file first (the render output path is relative).": ("请先保存 .blend 文件（渲染输出路径是相对路径）。",
                                                                       "請先儲存 .blend 檔案（渲染輸出路徑是相對路徑）。"),
    "Tick Object and/or Material IDs.": ("请勾选物体和/或材质 ID。", "請勾選物件和/或材質 ID。"),
    "The Object and Material layers need different names.": ("物体层与材质层需要不同的名称。", "物件圖層與材質圖層需要不同的名稱。"),
    "Choose the target collection (or set Targets to All).": ("请选择目标集合（或将目标设为“所有可见物体”）。",
                                                              "請選擇目標集合（或將目標設為「所有可見物件」）。"),
    "This Blender version has no per-view-layer world override (needs Blender 4.2+).":
        ("此 Blender 版本不支持按视图层覆盖世界环境（需要 Blender 4.2+）。", "此 Blender 版本不支援按視圖層覆寫世界環境（需要 Blender 4.2+）。"),
    "Nothing gets an ID (everything is glass, or has no material in Material-only mode).":
        ("没有任何物体获得 ID（全部是玻璃，或在仅材质模式下没有材质）。", "沒有任何物件獲得 ID（全部是玻璃，或在僅材質模式下沒有材質）。"),
}

# Blender looks strings up by (context, msgid). Labels and properties use the default context,
# operator names and buttons use "Operator"; register everything under both.
_CONTEXTS = ("*", "Operator")


def _build():
    hans, hant = {}, {}
    for en, (s, t) in STRINGS.items():
        for ctx in _CONTEXTS:
            hans[(ctx, en)] = s
            hant[(ctx, en)] = t
    # Blender 4.x used "zh_CN"/"zh_TW" in some builds; 4.2+ uses zh_HANS/zh_HANT
    return {"zh_HANS": hans, "zh_CN": hans, "zh_HANT": hant, "zh_TW": hant}


def register():
    try:
        bpy.app.translations.register(__package__, _build())
    except ValueError:      # already registered (Reload Scripts)
        bpy.app.translations.unregister(__package__)
        bpy.app.translations.register(__package__, _build())


def unregister():
    bpy.app.translations.unregister(__package__)
