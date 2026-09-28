import { AreaSelect } from "./AreaSelect.js";
import { ToolbarSelect } from "./ToolbarSelect.js";

/**
 * 订单工具条：唯凯站点 / 服务方式 / 运输种类 / 订舱操作 + 服务项目。
 *
 * 版式对齐 Figma（Frame 91 / 101 / 102~105 / 带 icon 按钮）：
 * 48px 白底横条、左右 24px；左半是四个 32px 胶囊（组间距 16px、胶囊间距 8px），
 * 右端是「服务项目」（与胶囊间距一致，8px）。
 * 最左边是订舱编号：提交成功后 poOrder 返回的编号（形如 BOAE2609240001PVG）显示在这里，
 * 与下方表头「项目」二字左对齐；没提交过时这一格为空，四个胶囊仍靠右对齐。
 *
 * 取值域与 poOrder 订单新增页（src/components/newOrderAdd.vue 的 basicinfoView）一致：
 * - 服务方式 opersystemdom：空运 / 海运 / 陆运 / 铁运 / 其它
 * - 运输种类 opersystem   ：出口 / 进口 / 国内
 * - 订舱操作 czlx         ：唯凯配舱（提交值 自货）/ 唯凯代操作（提交值 代操作）
 *                           —— 文案与提交值不同，所以下面用 { value, label }
 * - 唯凯站点 area         ：口径与 poOrder 的 areaSelect 一致，取站点字典
 *                           groupid == 101、按 ready04 分组；显示字典原文
 *                           （「上海丨SHA」），提交值是站点中文名。候选由后端
 *                           /analysis/sites 提供（见 AreaSelect.js）
 *
 * 这四项都来自上传时页面传入的 context，放在这里是为了让操作员在提交前能核对和修正——
 * 它们直接进提交报文，填错会挂错站点或业务类型。
 *
 * 下拉未选中时显示的是**字段名**（唯凯站点 / 服务方式 / …，深色），
 * 选中后显示取值并转为强调蓝。所以设计稿上第二个胶囊是蓝色的「空运」——
 * 那是已选中状态，其余三个仍是未选中的占位。
 */
export const OrderToolbar = {
  name: "OrderToolbar",
  components: { AreaSelect, ToolbarSelect },
  props: {
    order: { type: Object, required: true },
    // 唯凯站点候选：按分组传入（站点字典 groupid == 101），未接入时为空数组
    siteGroups: { type: Array, default: () => [] },
    // 已勾选的服务项目数（服务项目面板里勾的数量），显示在按钮上
    serviceCount: { type: Number, default: 0 },
    // 订舱编号：提交成功后 poOrder 返回，显示在工具条最左边（与表头「项目」左对齐）
    orderCode: { type: String, default: "" },
    locked: { type: Boolean, default: false },
  },
  emits: ["select-services"],
  data() {
    return {
      // label 给人看、value 进提交报文；两者相同时也写成一对，便于以后单独改文案
      serviceModes: [
        { value: "空运", label: "空运" },
        { value: "海运", label: "海运" },
        { value: "陆运", label: "陆运" },
        { value: "铁运", label: "铁运" },
        { value: "其它", label: "其它" },
      ],
      transportKinds: [
        { value: "出口", label: "出口" },
        { value: "进口", label: "进口" },
        { value: "国内", label: "国内" },
      ],
      // 与 poOrder 一致：界面显示「唯凯配舱 / 唯凯代操作」，提交的是 自货 / 代操作
      bookTypes: [
        { value: "自货", label: "唯凯配舱" },
        { value: "代操作", label: "唯凯代操作" },
      ],
    };
  },
  template: `
    <div class="doc-order-toolbar">
      <span
        v-if="orderCode"
        class="doc-dialog-order-code"
        :title="'订舱编号 ' + orderCode"
      >
        <i class="doc-dialog-order-code-dot" aria-hidden="true"></i>
        <span class="doc-dialog-order-code-text">{{ orderCode }}</span>
      </span>
      <div class="doc-order-group">
        <div class="doc-order-chips">
          <AreaSelect
            :model-value="order.area"
            :groups="siteGroups"
            placeholder="唯凯站点"
            title="唯凯站点"
            :disabled="locked"
            @update:model-value="order.area = $event"
          />
          <ToolbarSelect
            :model-value="order.opersystemdom"
            :options="serviceModes"
            placeholder="服务方式"
            title="服务方式"
            :disabled="locked"
            @update:model-value="order.opersystemdom = $event"
          />
          <ToolbarSelect
            :model-value="order.opersystem"
            :options="transportKinds"
            placeholder="运输种类"
            title="运输种类"
            :disabled="locked"
            @update:model-value="order.opersystem = $event"
          />
          <ToolbarSelect
            :model-value="order.czlx"
            :options="bookTypes"
            placeholder="订舱操作"
            title="订舱操作"
            :disabled="locked"
            @update:model-value="order.czlx = $event"
          />
        </div>
      </div>
      <button
        class="doc-order-services"
        type="button"
        :title="serviceCount ? '已选 ' + serviceCount + ' 项服务，点击查看 / 修改' : '勾选本票要做的服务（选填）'"
        @click="$emit('select-services')"
      >
        <i class="doc-order-services-icon" aria-hidden="true"></i>服务项目
        <span v-if="serviceCount" class="doc-order-services-count">{{ serviceCount }}</span>
      </button>
    </div>
  `,
};
