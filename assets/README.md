# assets/

第三方静态资源，随仓库一起分发以保证生成的 HTML **单文件离线可用**。

| 文件 | 说明 | 许可 |
| --- | --- | --- |
| `mathjax-tex-svg.js` | MathJax 3.2.2 `tex-svg` 组件（TeX 输入 + **SVG 输出**，不需要字体文件） | Apache-2.0 |

来源：`https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js`

渲染时由 `bili_summary.assemble.html.mathjax_block()` 内联进 HTML。
若该文件缺失，代码会自动退化为引用 CDN（此时需要联网）。
