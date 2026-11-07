<script setup>
/** 统一展示项目三态状态，避免页面直接暴露数据库内部状态。 */
import { computed } from "vue";
import { displayStatus, statusClass } from "../api/status";
const props = defineProps({ value: { type: String, default: "" } });
const label = computed(() => displayStatus(props.value));
const cssClass = computed(() => statusClass(props.value));
</script>
<template><span class="status-badge" :class="cssClass">{{ label }}</span></template>
