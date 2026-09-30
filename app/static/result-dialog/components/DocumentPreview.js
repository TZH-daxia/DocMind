const ACTIVE_BOX_ID = "doc-preview-active-box";
const MIN_ZOOM = 1;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;
// 等页面图片出结果的兜底时限：个别图片既不 load 也不 error（连接挂起）时，最多再等
// 这么久就把这一批当作加载完，免得弹窗的加载遮罩一直盖着出不来
const SETTLE_TIMEOUT_MS = 4000;

export const DocumentPreview = {
  name: "DocumentPreview",
  props: {
    pageUrls: { type: Array, default: () => [] },
    loading: { type: Boolean, default: false },
    error: { type: String, default: "" },
    highlightBoxes: { type: Array, default: () => [] },
    highlightStatus: { type: String, default: "" },
    highlightKey: { type: String, default: "" },
  },
  // settled：这一批页面图片是否都已出结果（加载成功或失败）。弹窗据此收起加载遮罩，
  // 保证"图片也都到位了才露出内容"，而不是先露出半张、剩下的再一张张蹦出来
  emits: ["settled"],
  data() {
    return {
      failedPages: {},
      // 已出结果的页面 url：每张图 load / error 各标一次
      settledUrls: {},
      settleTimer: null,
      scale: MIN_ZOOM,
      panState: null,
      panHandlers: null,
    };
  },
  computed: {
    pages() {
      return this.pageUrls.map((url, index) => ({
        url,
        pageNo: index + 1,
        boxes: this.highlightBoxes.filter((box) => Number(box.page) === index + 1),
      }));
    },
    focusedPage() {
      const first = this.highlightBoxes[0];
      return first ? Number(first.page) : 0;
    },
    isReview() {
      return this.highlightStatus === "needs_review" || this.highlightStatus === "conflict";
    },
    // 最小即原尺寸：缩小按钮在 100% 时禁用
    isZoomed() {
      return this.scale > MIN_ZOOM;
    },
    canZoomIn() {
      return this.scale < MAX_ZOOM;
    },
    canZoomOut() {
      return this.scale > MIN_ZOOM;
    },
    zoomPercent() {
      return Math.round(this.scale * 100);
    },
    pageItemStyle() {
      return {
        width: `${this.scale * 100}%`,
        // 放大后放开原尺寸上限，否则宽度被 max-width 卡住
        maxWidth: this.isZoomed ? "none" : "",
      };
    },
    // 这一批页面是否都出了结果；页数为 0（没有要等的图）时视为完成
    allSettled() {
      return this.pageUrls.every((url) => this.settledUrls[url]);
    },
  },
  watch: {
    // 加载状态一变就上报（immediate：一挂载父组件就有确定值）
    allSettled: {
      immediate: true,
      handler(value) {
        this.$emit("settled", value);
      },
    },
    highlightKey() {
      this.$nextTick(() => this.scrollToHighlight());
    },
    // 换任务：回到原始大小、清掉上一批的加载结果（于是 allSettled 变 false、遮罩继续盖住），
    // 并启动超时兜底
    pageUrls() {
      this.settledUrls = {};
      this.setScale(MIN_ZOOM);
      this.resetScroll();
      clearTimeout(this.settleTimer);
      this.settleTimer = setTimeout(() => this.settleAll(), SETTLE_TIMEOUT_MS);
    },
  },
  beforeUnmount() {
    clearTimeout(this.settleTimer);
    this.stopPan();
  },
  methods: {
    markFailed(url) {
      this.failedPages[url] = true;
      this.settledUrls[url] = true;
    },
    markLoaded(url) {
      this.failedPages[url] = false;
      this.settledUrls[url] = true;
    },
    // 超时兜底：把所有页面直接标成已出结果，别让遮罩一直盖着
    settleAll() {
      this.pageUrls.forEach((url) => {
        this.settledUrls[url] = true;
      });
    },
    boxId(page, index) {
      return page.pageNo === this.focusedPage && index === 0 ? ACTIVE_BOX_ID : null;
    },
    boxStyle(box) {
      const [x, y, width, height] = box.bbox;
      // 细窄的文字框加一点外扩，避免看起来像一条线
      const padX = width * 0.04;
      const padY = Math.max(height * 0.3, 0.002);
      let left = x - padX;
      let top = y - padY;
      let boxWidth = width + padX * 2;
      let boxHeight = height + padY * 2;
      // 短值（如"74"）的框太小，给一个最小可见尺寸
      const minWidth = 0.016;
      const minHeight = 0.013;
      if (boxWidth < minWidth) {
        left -= (minWidth - boxWidth) / 2;
        boxWidth = minWidth;
      }
      if (boxHeight < minHeight) {
        top -= (minHeight - boxHeight) / 2;
        boxHeight = minHeight;
      }
      return {
        left: `${left * 100}%`,
        top: `${top * 100}%`,
        width: `${boxWidth * 100}%`,
        height: `${boxHeight * 100}%`,
      };
    },
    scrollToHighlight() {
      const target = document.getElementById(ACTIVE_BOX_ID);
      if (target) {
        target.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    },
    zoomIn() {
      this.setScale(this.scale + ZOOM_STEP);
    },
    zoomOut() {
      this.setScale(this.scale - ZOOM_STEP);
    },
    resetZoom() {
      this.setScale(MIN_ZOOM);
      this.resetScroll();
    },
    setScale(next) {
      this.scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(next.toFixed(2))));
      if (!this.isZoomed) {
        this.stopPan();
      }
    },
    resetScroll() {
      this.$nextTick(() => {
        const body = this.$refs.body;
        if (!body) return;
        body.scrollLeft = 0;
        body.scrollTop = 0;
      });
    },
    // 放大后才允许拖动查看：按下记录起点，移动时反向平移滚动位置
    onPanStart(event) {
      if (!this.isZoomed || event.button !== 0) return;
      const body = this.$refs.body;
      if (!body) return;
      event.preventDefault();
      this.panState = {
        x: event.clientX,
        y: event.clientY,
        left: body.scrollLeft,
        top: body.scrollTop,
      };
      this.panHandlers = {
        move: (moveEvent) => this.onPanMove(moveEvent),
        up: () => this.stopPan(),
      };
      window.addEventListener("mousemove", this.panHandlers.move);
      window.addEventListener("mouseup", this.panHandlers.up);
    },
    onPanMove(event) {
      const body = this.$refs.body;
      if (!body || !this.panState) return;
      body.scrollLeft = this.panState.left - (event.clientX - this.panState.x);
      body.scrollTop = this.panState.top - (event.clientY - this.panState.y);
    },
    stopPan() {
      if (this.panHandlers) {
        window.removeEventListener("mousemove", this.panHandlers.move);
        window.removeEventListener("mouseup", this.panHandlers.up);
      }
      this.panHandlers = null;
      this.panState = null;
    },
  },
  template: `
    <section class="doc-dialog-preview">
      <header class="doc-dialog-preview-head">
        <span class="doc-dialog-eyebrow">SOURCE DOCUMENT</span>
        <div class="doc-dialog-preview-tools">
          <small v-if="pageUrls.length">共 {{ pageUrls.length }} 页</small>
          <div class="doc-dialog-zoom">
            <button
              class="doc-dialog-zoom-button"
              type="button"
              title="缩小"
              :disabled="!canZoomOut"
              @click="zoomOut"
            >−</button>
            <button
              class="doc-dialog-zoom-value"
              type="button"
              title="恢复原始大小"
              :disabled="!isZoomed"
              @click="resetZoom"
            >{{ zoomPercent }}%</button>
            <button
              class="doc-dialog-zoom-button"
              type="button"
              title="放大"
              :disabled="!canZoomIn"
              @click="zoomIn"
            >＋</button>
          </div>
        </div>
      </header>
      <div
        ref="body"
        class="doc-dialog-preview-body"
        :class="{ 'is-zoomed': isZoomed, 'is-panning': Boolean(panState) }"
        @mousedown="onPanStart"
      >
        <p v-if="error" class="doc-dialog-empty">{{ error }}</p>
        <p v-else-if="loading" class="doc-dialog-empty">正在加载原件…</p>
        <p v-else-if="!pageUrls.length" class="doc-dialog-empty">暂无页面图片</p>
        <template v-else>
          <figure
            v-for="page in pages"
            :key="page.url"
            class="doc-dialog-page-item"
            :style="pageItemStyle"
          >
            <div class="doc-dialog-page-wrap">
              <img
                class="doc-dialog-page"
                :src="page.url"
                :alt="'第 ' + page.pageNo + ' 页'"
                draggable="false"
                fetchpriority="high"
                decoding="async"
                @load="markLoaded(page.url)"
                @error="markFailed(page.url)"
              >
              <span
                v-for="(box, index) in page.boxes"
                :key="page.pageNo + '-' + index"
                :id="boxId(page, index)"
                class="doc-highlight"
                :class="{ 'is-review': isReview }"
                :style="boxStyle(box)"
              ></span>
            </div>
            <figcaption class="doc-dialog-page-caption">
              <span>第 {{ page.pageNo }} 页</span>
              <span v-if="failedPages[page.url]" class="doc-dialog-error">
                图片加载失败，请确认服务已重启并包含页面图片接口
                <a :href="page.url" target="_blank" rel="noopener">在新窗口打开</a>
              </span>
            </figcaption>
          </figure>
        </template>
      </div>
    </section>
  `,
};
