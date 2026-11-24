/** Element Plus 组件按需注册。
 *
 * `app.use(ElementPlus)` 会把整库组件注册进来（首屏 JS 明显变大）；这里只注册模板中
 * 实际使用的组件，并保留完整样式表（element-plus/dist/index.css 在 main.js 引入），
 * 因此视觉与交互行为和全量注册完全一致。
 *
 * 迁移/新增页面用到新的 `el-*` 组件时，必须在此补一行，否则组件不会渲染。
 */
import {
  ElAlert,
  ElAside,
  ElBadge,
  ElButton,
  ElCard,
  ElCheckbox,
  ElCheckboxGroup,
  ElCollapse,
  ElCollapseItem,
  ElContainer,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDivider,
  ElDrawer,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElHeader,
  ElIcon,
  ElImage,
  ElInput,
  ElInputNumber,
  ElMain,
  ElMenu,
  ElMenuItem,
  ElOption,
  ElPagination,
  ElPopconfirm,
  ElProgress,
  ElRadio,
  ElRadioButton,
  ElRadioGroup,
  ElResult,
  ElSelect,
  ElSkeleton,
  ElSlider,
  ElStep,
  ElSteps,
  ElSwitch,
  ElTabPane,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTag,
  ElTimeSelect,
  ElTooltip,
  ElConfigProvider,
  ElLoading,
} from 'element-plus'
import { defineComponent, h } from 'vue'

/** 弹窗/抽屉统一 teleport 到 body。
 *
 * 全站卡片使用玻璃拟态 `backdrop-filter`，而 `backdrop-filter`/`filter`/`transform`
 * 的祖先会为 `position: fixed` 后代创建包含块：遮罩层会以该祖先为「视口」定位，
 * 于是弹窗既不居中（页面滚动后甚至跑到可视区外），还会被祖先的 overflow 裁剪。
 * Element Plus 的 append-to-body 默认关闭，这里统一打开，保证弹窗始终相对真实视口定位。
 * 组件如显式传入 append-to-body 仍以传入值为准。
 */
const appendedToBody = (Comp, name) => defineComponent({
  name,
  inheritAttrs: false,
  setup(_props, { attrs, slots }) {
    return () => h(Comp, { appendToBody: true, ...attrs }, slots)
  },
})

const AppDialog = appendedToBody(ElDialog, 'ElDialog')
const AppDrawer = appendedToBody(ElDrawer, 'ElDrawer')

const components = {
  ElAlert,
  ElAside,
  ElBadge,
  ElButton,
  ElCard,
  ElCheckbox,
  ElCheckboxGroup,
  ElCollapse,
  ElCollapseItem,
  ElContainer,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDivider,
  ElDrawer,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElHeader,
  ElIcon,
  ElImage,
  ElInput,
  ElInputNumber,
  ElMain,
  ElMenu,
  ElMenuItem,
  ElOption,
  ElPagination,
  ElPopconfirm,
  ElProgress,
  ElRadio,
  ElRadioButton,
  ElRadioGroup,
  ElResult,
  ElSelect,
  ElSkeleton,
  ElSlider,
  ElStep,
  ElSteps,
  ElSwitch,
  ElTabPane,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTag,
  ElTimeSelect,
  ElTooltip,
  ElConfigProvider,
}

// 弹窗/抽屉改用 teleport 到 body 的包装版（见上方 appendedToBody 说明）
components.ElDialog = AppDialog
components.ElDrawer = AppDrawer

export default {
  install(app) {
    for (const [name, comp] of Object.entries(components)) app.component(name, comp)
    // v-loading 指令（DirPicker / Files / Subscriptions 等页面在用）
    app.use(ElLoading)
  },
}
