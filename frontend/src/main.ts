import { createApp } from "vue";
import "cesium/Build/Cesium/Widgets/widgets.css";
import "element-plus/dist/index.css";
import "./style.css";
import ElementPlus from "element-plus";
import App from "./App.vue";

createApp(App).use(ElementPlus).mount("#app");
