import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

/**
 * 轻量级 ESLint 配置（对齐后端 ruff 原则）：
 * 仅启用能抓出真实故障的静态检查（未定义变量、模板语法错、重复键、不可达代码等），
 * 不强制代码风格（引号/分号/缩进/换行），避免制造无关 diff。
 */
export default [
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    files: ['src/**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      // 允许单字组件名（Login.vue / Dashboard.vue / Files.vue 等既有命名）
      'vue/multi-word-component-names': 'off',
      // 表单分区子组件模式下，允许对传入的表单对象属性直接双向绑定（禁止直接重写 prop 本身）
      'vue/no-mutating-props': ['error', { shallowOnly: true }],
      // 允许空 catch 块（前端常见兜底 try { ... } catch {}）
      'no-empty': ['error', { allowEmptyCatch: true }],
      // 未使用变量仅报提示，忽略下划线前缀占位参数
      'no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
    },
  },
  {
    files: ['public/sw.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'script',
      globals: {
        ...globals.serviceworker,
      },
    },
    rules: {
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
]
