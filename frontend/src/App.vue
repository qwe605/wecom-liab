<!--
  Vue 根组件只提供路由出口。侧边栏和审核台各自管理页面状态，避免在根组件中
  混入跨角色业务逻辑。
-->
<template><router-view /></template>
