<script setup>
/** 递归分类目录节点：只负责展开状态和分类选择，不加载 Skill 正文。 */
defineProps({
  nodes: { type: Array, default: () => [] },
  depth: { type: Number, default: 0 },
  parentPath: { type: Array, default: () => [] },
  expandedKeys: { type: Object, required: true },
  selectedKey: { type: String, default: "" },
});
const emit = defineEmits(["toggle", "select"]);

function nodeKey(path) {
  return path.map((item) => `${item.level}:${item.code}`).join("/");
}
</script>

<template>
  <template v-for="node in nodes" :key="nodeKey([...parentPath, node])">
    <div
      class="wiki-tree-node"
      :class="{ selected: selectedKey === nodeKey([...parentPath, node]) }"
      :style="{ paddingLeft: `${10 + depth * 18}px` }"
    >
      <button
        v-if="node.children?.length"
        class="tree-toggle"
        :title="expandedKeys.has(nodeKey([...parentPath, node])) ? '收起分类' : '展开分类'"
        @click.stop="emit('toggle', nodeKey([...parentPath, node]))"
      >{{ expandedKeys.has(nodeKey([...parentPath, node])) ? "▾" : "▸" }}</button>
      <span v-else class="tree-toggle-placeholder"></span>
      <button class="tree-node-label" @click="emit('select', { node, path: [...parentPath, node], key: nodeKey([...parentPath, node]) })">
        <span>{{ node.name }}</span><small>{{ node.skill_count }}</small>
      </button>
    </div>
    <TaxonomyTreeNode
      v-if="node.children?.length && expandedKeys.has(nodeKey([...parentPath, node]))"
      :nodes="node.children"
      :depth="depth + 1"
      :parent-path="[...parentPath, node]"
      :expanded-keys="expandedKeys"
      :selected-key="selectedKey"
      @toggle="emit('toggle', $event)"
      @select="emit('select', $event)"
    />
  </template>
</template>
