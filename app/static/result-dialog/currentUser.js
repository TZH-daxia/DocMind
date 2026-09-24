/**
 * 当前操作人（登录名）与票据：开发期直接取 poOrder 的登录态。
 *
 * 口径：提交报文里的 `czman`（当前用户）与 `customerRelList[].addman`（创建人）是同一个值，
 * 就是登录名。生产环境由官网/客服在调用 DocMind 时显式传入（需求：使用托书识别前先登录），
 * 这里只是开发期的兜底——取不到就返回空串，后端会按 poOrder 口径驳回
 * 「无操作人数据，请重新登录」。
 *
 * 取值顺序：
 * 1. URL 参数 `?czman=xxx`：跨端口 / 跨域时唯一可靠的通道；
 * 2. Cookie `usrname`：poOrder 登录时会写入（`login.vue` 的 `setCookie('usrname', ...)`），
 *    Cookie 不隔离端口，两边用同一主机名（都开 127.0.0.1）即可读到。
 *
 * 注意：localStorage / sessionStorage 按「协议 + 主机 + 端口」隔离，读不到 poOrder 的
 * `usrname` / `ticket`，所以票据这一项开发期只能靠 `?ticket=` 传。
 * 另外 Cookie 是明文可改的：它只是开发便利，不是安全依据。
 */

function fromQuery(name) {
  try {
    return (new URLSearchParams(window.location.search).get(name) || "").trim();
  } catch {
    return "";
  }
}

function fromCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]).trim() : "";
}

/** 当前操作人（登录名）；取不到返回空串。 */
export function currentUserName() {
  return fromQuery("czman") || fromCookie("usrname");
}

/**
 * 操作人所在部门（poOrder 里是 `localStorage.dom`）；没有则返回空串。
 *
 * 提交报文里的 `dom` 决定这单归哪个部门，而 poOrder 的列表查询默认按部门过滤；
 * 开发期只能靠 URL 参数传（`?dom=xxx`）——poOrder 只把它存在 localStorage 里，
 * 跨端口读不到。缺省时后端会回落「出口部」（poOrder 自己的默认值）。
 */
export function currentUserDom() {
  return fromQuery("dom");
}

/** poOrder 票据；没有则返回空串（提交接口需要鉴权时才用得上）。 */
export function currentTicket() {
  return fromQuery("ticket");
}
