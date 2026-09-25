/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 * OpsForge note : 基于 v4.0.1 原件，仅追加移除 Service Worker 生成插件——
 * react-scripts 3.4.3 的 WorkboxWebpackPlugin.GenerateSW（workbox-build@4 模板）在
 * node 18 下报 "assignWith is not defined" 构建失败；spug src/index.js 调
 * serviceWorker.unregister()，运行时本就不用 SW，去掉对产物行为零变更。
 */
const {override, addDecoratorsLegacy, addLessLoader} = require('customize-cra');

// new WorkboxWebpackPlugin.GenerateSW() 实例的 constructor.name 是 'GenerateSW'，
// 按实例类名过滤（含旧 sw-precache 名单兜底），而非 require 插件包
const SW_PLUGIN_NAMES = ['GenerateSW', 'InjectManifest', 'WorkboxWebpackPlugin', 'SWPrecacheWebpackPlugin'];

module.exports = override(
  addDecoratorsLegacy(),
  addLessLoader({
    lessOptions: {
      javascriptEnabled: true,
      modifyVars: {
        '@primary-color': '#2563fc'
      }
    }
  }),
  (config) => {
    config.plugins = config.plugins.filter(
      plugin => !plugin || !SW_PLUGIN_NAMES.includes(plugin.constructor.name));
    return config;
  },
);
