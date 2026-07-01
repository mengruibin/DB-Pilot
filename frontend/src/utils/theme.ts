/**
 * Naive UI 主题覆盖配置
 *
 * 将 Naive UI 的 darkTheme 与 Onyx Console 设计语言对齐
 */
import type { GlobalThemeOverrides } from 'naive-ui'

export const themeOverrides: GlobalThemeOverrides = {
  common: {
    // 主色调 → 青绿
    primaryColor: '#2DD4BF',
    primaryColorHover: '#5EE4D0',
    primaryColorPressed: '#14B8A6',
    primaryColorSuppl: '#2DD4BF',

    // 信息色 → 蓝
    infoColor: '#38BDF8',
    infoColorHover: '#7DD3FC',
    infoColorPressed: '#0284C7',

    // 成功色 → 翠绿
    successColor: '#34D399',
    successColorHover: '#6EE7B7',
    successColorPressed: '#059669',

    // 警告色 → 琥珀
    warningColor: '#FBBF24',
    warningColorHover: '#FCD34D',
    warningColorPressed: '#D97706',

    // 错误色 → 玫瑰红
    errorColor: '#F87171',
    errorColorHover: '#FCA5A5',
    errorColorPressed: '#DC2626',

    // 背景
    bodyColor: '#0B0E14',
    cardColor: '#141922',
    modalColor: '#141922',
    popoverColor: '#1A2132',
    tableColor: '#141922',

    // 输入控件
    inputColor: '#141922',
    inputColorDisabled: '#1A2132',

    // 边框
    borderColor: '#1E293B',
    hoverColor: '#1C2333',

    // 文字
    textColor1: '#E2E8F0',
    textColor2: '#94A3B8',
    textColor3: '#64748B',
    textColorDisabled: '#475569',

    // 字号
    fontSize: '14px',
    fontSizeSmall: '12px',
    fontSizeMedium: '14px',
    fontSizeLarge: '16px',

    // 字体
    fontFamily: "'DM Sans', sans-serif",
    fontFamilyMono: "'JetBrains Mono', monospace",

    // 圆角
    borderRadius: '6px',
    borderRadiusSmall: '4px',

    // 阴影
    boxShadow1: '0 1px 2px rgba(0, 0, 0, 0.3)',
    boxShadow2: '0 4px 12px rgba(0, 0, 0, 0.4)',
    boxShadow3: '0 8px 24px rgba(0, 0, 0, 0.5)',
  },

  // 按钮
  Button: {
    textColorGhostPrimary: '#2DD4BF',
    borderPrimary: '#2DD4BF',
    colorPrimary: '#2DD4BF',
    textColorPrimary: '#0B0E14',
    fontWeight: '500',
    borderRadiusMedium: '6px',
  },

  // 输入框
  Input: {
    borderHover: '#334155',
    borderFocus: '#2DD4BF',
    colorFocus: '#141922',
    textColor: '#E2E8F0',
    placeholderColor: '#64748B',
    borderRadius: '6px',
    heightMedium: '36px',
  },

  // 下拉选择
  Select: {
    peers: {
      InternalSelection: {
        borderHover: '#334155',
        borderFocus: '#2DD4BF',
        heightMedium: '36px',
      },
    },
  },

  // 表格
  DataTable: {
    thColor: '#1A2132',
    tdColor: '#141922',
    borderColor: '#1E293B',
    thTextColor: '#94A3B8',
    tdTextColor: '#E2E8F0',
    borderRadius: '6px',
  },

  // 对话框
  Dialog: {
    contentColor: '#141922',
    headerColor: '#141922',
    borderRadius: '8px',
  },

  // 卡片
  Card: {
    color: '#141922',
    borderColor: '#1E293B',
    borderRadius: '8px',
    titleTextColor: '#E2E8F0',
  },

  // 折叠面板
  Collapse: {
    titleFontWeight: '500',
    titleTextColor: '#94A3B8',
    titleTextColorActive: '#E2E8F0',
    dividerColor: '#1E293B',
  },

  // 开关
  Switch: {
    railColorActive: '#2DD4BF',
    buttonColor: '#FFF',
  },

  // 消息/通知
  Message: {
    color: '#1A2132',
    textColor: '#E2E8F0',
    borderRadius: '6px',
  },

  // 标签
  Tag: {
    borderRadius: '4px',
  },
}
