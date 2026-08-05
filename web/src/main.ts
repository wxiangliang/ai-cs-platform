import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import * as ElementPlusIconsVue from "@element-plus/icons-vue";

import App from "./App.vue";
import router from "./router";

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(ElementPlus, { locale: zhCn });
// 图标全局注册（菜单 meta.icon 按名称动态渲染需要）
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component);
}
app.mount("#app");
