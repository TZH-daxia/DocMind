/**
 * 业务系统（报文里的 `system`）：**服务方式首位 + 运输种类首位**。
 *
 * 按 poOrder 的字段命名（本项目工具条同此口径）：
 * - `opersystem`     = 运输种类（出口 / 进口 / 国内）
 * - `opersystemdom`  = 服务方式（空运 / 海运 / 陆运 / 铁运 / 其它）
 *
 * 于是「空运 + 出口」→「空出」，与 poOrder 自己的算法一致
 * （`newOrderAdd.vue` 的 serviceList 传参：`opersystemdom.substr(0,1) + system.substr(0,1)`，
 * 其中 poOrder 的 `system` 就是运输种类的值）。「服务方式 = 其它」或
 * 「运输种类 = 国内」统一为「国内服务」。
 *
 * 与后端 `app/service/order_submit_service.py` 的 `compute_system` 是同一规则，
 * 前端用在两处：信控查询的 `system` 参数、「本票默认联系人」的匹配条件。
 */
export function derivedSystem(order) {
  const kind = String(order?.opersystem || "");
  const mode = String(order?.opersystemdom || "");
  if (mode === "其它" || kind === "国内") {
    return "国内服务";
  }
  return `${mode.slice(0, 1)}${kind.slice(0, 1)}`;
}
