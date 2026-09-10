export const DocumentPreview = {
  name: "DocumentPreview",
  props: {
    fileName: { type: String, default: "" },
    pageUrls: { type: Array, default: () => [] },
    loading: { type: Boolean, default: false },
    error: { type: String, default: "" },
  },
  data() {
    return { failedPages: {} };
  },
  methods: {
    markFailed(url) {
      this.failedPages[url] = true;
    },
    markLoaded(url) {
      this.failedPages[url] = false;
    },
  },
  template: `
    <section class="doc-dialog-preview">
      <header class="doc-dialog-preview-head">
        <span class="doc-dialog-eyebrow">SOURCE DOCUMENT</span>
        <strong>{{ fileName || "原件预览" }}</strong>
        <small v-if="pageUrls.length">共 {{ pageUrls.length }} 页</small>
      </header>
      <div class="doc-dialog-preview-body">
        <p v-if="error" class="doc-dialog-empty">{{ error }}</p>
        <p v-else-if="loading" class="doc-dialog-empty">正在加载原件…</p>
        <p v-else-if="!pageUrls.length" class="doc-dialog-empty">暂无页面图片</p>
        <template v-else>
          <figure v-for="(url, index) in pageUrls" :key="url" class="doc-dialog-page-item">
            <img
              class="doc-dialog-page"
              :src="url"
              :alt="'第 ' + (index + 1) + ' 页'"
              @load="markLoaded(url)"
              @error="markFailed(url)"
            >
            <figcaption class="doc-dialog-page-caption">
              <span>第 {{ index + 1 }} 页</span>
              <span v-if="failedPages[url]" class="doc-dialog-error">
                图片加载失败，请确认服务已重启并包含页面图片接口
                <a :href="url" target="_blank" rel="noopener">在新窗口打开</a>
              </span>
            </figcaption>
          </figure>
        </template>
      </div>
    </section>
  `,
};
