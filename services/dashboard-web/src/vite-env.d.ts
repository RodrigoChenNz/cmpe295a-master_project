/// <reference types="vite/client" />

/* The two variables this app reads. Declared, so a typo in a component is a
 * type error rather than an undefined at runtime. */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_WS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
