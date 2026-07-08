# PPT 模板目录

把设计好的 `.pptx` 文件放进这个目录，即可在 spec 里通过 `"template": "<文件名>"`
（带不带 `.pptx` 后缀都行）选用。`build.py` 会 `Presentation(<该文件>)` 作为基底，
沿用模板里的母版、配色、字体与版式，再往里填充内容。

## 用法

```json
{ "template": "business", "theme": "business-blue", "slides": [ ... ] }
```

- `build.py` 通过**版式名**匹配版式（"Title Slide" / "Title and Content" /
  "Title Only" 等），而不是写死索引，所以自定义模板只要版式命名合理即可工作；
  匹配不到时回退到位置索引，仍可生成。
- 路径受白名单保护：只接受本目录内的文件名，找不到/加载失败时自动回退到内置默认模板，
  不会让整次生成失败。

## 制作模板

在 PowerPoint / Keynote / WPS 里设计好主题（配色、字体、母版背景、Logo），
另存为 `.pptx`（无需内容页，保留版式即可），放到此目录。建议保留这些标准版式名：
`Title Slide`、`Title and Content`、`Title Only`、`Blank`。

> 说明：仓库未内置二进制模板文件。不放任何 `.pptx` 时，`template` 字段会被忽略，
> 走内置默认模板 + `theme` 代码化配色，同样能得到丰富生动的效果。
