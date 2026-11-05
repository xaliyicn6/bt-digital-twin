/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 天地图 key；留空时降级为 OSM */
  readonly VITE_TIANDITU_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, unknown>;
  export default component;
}
