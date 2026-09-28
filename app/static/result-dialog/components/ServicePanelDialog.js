import {
  BOOKING_AGENT,
  BOOKING_PICKUP,
  SERVICE_BOARDS,
  countPickedServices,
} from "../serviceItems.js";

/**
 * 服务项目面板：勾选本票要做的服务（可多选）。
 *
 * 版式对齐 poOrder 订单新增页的「服务项目（选填）」模块
 * （src/components/orderDetails/serviceList.vue + templates/serviceItem.vue）：
 * 板块 → 类别分列 → 每项一个复选框。
 *
 * 「主营」那一格是特例：OA0010 给出两个选项（唯凯配舱 / 唯凯代操作），显示名与选中项
 * 都跟着工具条的「订舱操作」（报文 czlx）走 —— 与 serviceItem.getTitle 一致；
 * 这一组在面板里是**灰色不可点**的，要改就改工具条。
 *
 * 其余服务勾选立即生效（点一下即算，再点一次取消），所以底部只有关闭；勾选结果由父组件
 * 落到 dialogState.serviceCodes，提交时按面板顺序进报文的 serviceList。
 */
export const ServicePanelDialog = {
  name: "ServicePanelDialog",
  props: {
    open: { type: Boolean, default: false },
    // 已选服务代码
    modelValue: { type: Array, default: () => [] },
    // 订舱操作（报文 czlx）：自货 = 唯凯配舱、代操作 = 唯凯代操作，决定 OA0010 选的是哪一项
    bookingType: { type: String, default: "" },
    // 已提交：只能看不能改
    locked: { type: Boolean, default: false },
  },
  emits: ["update:modelValue", "close"],
  data() {
    return { boards: SERVICE_BOARDS };
  },
  computed: {
    selected() {
      return Array.isArray(this.modelValue) ? this.modelValue : [];
    },
    // 配舱服务当前落在哪个选项上：只有「代操作」才算唯凯代操作，其余（含空值）算唯凯配舱
    bookingVariant() {
      return this.bookingType === BOOKING_AGENT ? BOOKING_AGENT : BOOKING_PICKUP;
    },
    // 底部「已选 N 项」：只算操作员主动勾的（配舱服务由「订舱操作」决定，不计入）
    pickedCount() {
      return countPickedServices(this.selected);
    },
  },
  methods: {
    isChecked(item) {
      if (item.variant) {
        // 配舱服务这一组跟随「订舱操作」：两个选项永远恰好选中一个
        return this.bookingVariant === item.variant;
      }
      return this.selected.includes(item.code);
    },
    // 灰色不可点的项：已提交整表锁定；配舱服务那一组始终由「订舱操作」决定，面板里不改
    isLocked(item) {
      return this.locked || Boolean(item.variant);
    },
    toggle(item) {
      if (this.locked || item.variant) {
        return;
      }
      const picked = new Set(this.selected);
      if (picked.has(item.code)) {
        picked.delete(item.code);
      } else {
        picked.add(item.code);
      }
      this.$emit("update:modelValue", [...picked]);
    },
    close() {
      this.$emit("close");
    },
  },
  template: `
    <div v-if="open" class="doc-service-layer">
      <div class="doc-service-backdrop" @click="close"></div>
      <section class="doc-service-panel" role="dialog" aria-modal="true" aria-label="服务项目">
        <header class="doc-service-head">
          <div class="doc-service-head-text">
            <h2 class="doc-service-title">服务项目<small>（选填）</small></h2>
          </div>
          <!-- 关闭按钮直接复用上一级弹窗的圆形叉号样式（.doc-dialog-collapse + 同一个图标） -->
          <button
            class="doc-dialog-collapse"
            type="button"
            aria-label="关闭"
            title="关闭"
            @click="close"
          ><i class="doc-dialog-collapse-icon" aria-hidden="true"></i></button>
        </header>
        <div class="doc-service-body">
          <section v-for="board in boards" :key="board.title" class="doc-service-board">
            <h3 class="doc-service-board-title">{{ board.title }}</h3>
            <div class="doc-service-groups">
              <div v-for="group in board.groups" :key="group.title" class="doc-service-group">
                <p class="doc-service-group-title">{{ group.title }}</p>
                <ul class="doc-service-list">
                  <li v-for="item in group.items" :key="item.label">
                    <label
                      class="doc-service-entry"
                      :class="{ 'is-checked': isChecked(item), 'is-disabled': isLocked(item) }"
                    >
                      <input
                        type="checkbox"
                        :checked="isChecked(item)"
                        :disabled="isLocked(item)"
                        @change="toggle(item)"
                      />
                      <span class="doc-service-entry-name">{{ item.label }}</span>
                    </label>
                  </li>
                </ul>
              </div>
            </div>
          </section>
        </div>
        <footer class="doc-service-foot">
          <span class="doc-service-count">已选 <b>{{ pickedCount }}</b> 项</span>
          <button class="doc-service-done" type="button" @click="close">完成</button>
        </footer>
      </section>
    </div>
  `,
};
