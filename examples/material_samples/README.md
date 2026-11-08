# V50 四类素材样例（需人工提供片源）

V50 的验收内容是「手绘 / 2D / 3D / 实拍 **四类素材各一份**，能被素材系统识别并按类落位」。
**片源必须由人提供**：四类素材的授权与出处要能追溯，脚本不会、也不该凭空生成
"看起来像实拍"的东西（那只是伪造证据，不解决任何问题）。

## 目录约定

```
examples/material_samples/
├── hand_drawn/      # 手绘：线稿/素描/水彩 扫描或导出
├── 2d/              # 2D 动画：扁平插画、卡通、MG 帧
├── 3d/              # 3D：渲染图/建模截图
└── live_action/     # 实拍：手机/相机原片
```

- 每类至少 1 个文件；支持 `.png/.jpg/.jpeg/.webp/.gif/.mp4/.mov`。
- 文件名建议带类别与内容关键词（如 `hand_drawn_ink_city.png`）——本地素材源
  按**文件名 + 标签**做关键词打分，命名越贴近内容，检索越准。
- 大文件不要进仓库：把目录指到本地素材盘即可（`--dir` 或
  `CLIPWRIGHT_LOCAL_MATERIAL_DIR`）。

## 校验

```powershell
python scripts/check_v50_material_samples.py                  # 默认 examples/material_samples
python scripts/check_v50_material_samples.py --dir D:\素材\四类样例
```

脚本做三件事：①每类是否至少 1 个文件；②用真实实现 `LocalFolderSource`
（`local_material` 插件的底层）按每类关键词检索，断言能命中**本类**文件；
③检查是否"串味"（某类查询命中别的类文件 → 关键词区分度不足，报告里会列出）。
结果写 `out/v50/report.json`，任一类缺失退出码 1 并打印缺哪一类。

## 与管线的连接

`local_material` 插件（`plugins/local_material/`）把该目录注册成素材源：
环境变量 `CLIPWRIGHT_LOCAL_MATERIAL_DIR` 指向它之后，素材 Agent 检索时就会带上
本地文件，落位与网络素材完全同一条路径（sha256 去重、used_count 统计都生效）。
