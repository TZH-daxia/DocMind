import { resultDialog } from "./composables/useResultDialog.js";
import { ResultDialog } from "./components/ResultDialog.js";

window.DocMindResultDialog = {
  open: (taskId) => resultDialog.open(taskId),
  close: () => resultDialog.close(),
};

// 表格区的纵向滚动条会占掉内容宽度，而工具条 / 底部操作条是横跨整栏、不跟着表格滚动的，
// 所以它们的右边界会比输入框多出一个滚动条宽度（Windows 经典滚动条约 15px，macOS overlay 滚动条为 0）。
// 这个宽度随平台变化、CSS 里读不到，因此启动时量一次写进 --doc-scrollbar-w，
// 供 styles.css 里底部操作条的右侧内边距使用（见那里的 calc）
function measureScrollbarWidth() {
  const probe = document.createElement("div");
  probe.style.cssText =
    "position:absolute;top:-9999px;width:100px;height:100px;overflow:scroll;";
  document.body.appendChild(probe);
  const width = probe.offsetWidth - probe.clientWidth;
  probe.remove();
  return width;
}

document.documentElement.style.setProperty(
  "--doc-scrollbar-w",
  `${measureScrollbarWidth()}px`,
);

const root = document.querySelector("#resultDialogRoot");
if (root && window.Vue) {
  window.Vue.createApp(ResultDialog).mount(root);
}
