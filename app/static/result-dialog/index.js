import { resultDialog } from "./composables/useResultDialog.js";
import { ResultDialog } from "./components/ResultDialog.js";

window.DocMindResultDialog = {
  open: (taskId) => resultDialog.open(taskId),
  close: () => resultDialog.close(),
};

const root = document.querySelector("#resultDialogRoot");
if (root && window.Vue) {
  window.Vue.createApp(ResultDialog).mount(root);
}
