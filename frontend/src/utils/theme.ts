/**
 * Naive UI 主题覆盖配置（深色）
 *
 * 将 Naive UI 的 darkTheme 与 DB-Pilot 设计语言对齐
 */
import type { GlobalThemeOverrides } from 'naive-ui'

export const darkThemeOverrides: GlobalThemeOverrides = {
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

/**
 * Naive UI 主题覆盖配置（浅色）
 *
 * 将 Naive UI 的 lightTheme 与 DB-Pilot 浅色设计规范对齐
 */
export const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    // 主色调 → 深石板灰（浅色主题灰黑白简约配色）
    primaryColor: '#1E293B',
    primaryColorHover: '#334155',
    primaryColorPressed: '#0F172A',
    primaryColorSuppl: '#1E293B',

    // 信息色
    infoColor: '#38BDF8',
    infoColorHover: '#7DD3FC',
    infoColorPressed: '#0284C7',

    // 成功色 → 中深灰（浅色主题去除绿色）
    successColor: '#334155',
    successColorHover: '#475569',
    successColorPressed: '#1E293B',

    // 警告色
    warningColor: '#FBBF24',
    warningColorHover: '#FCD34D',
    warningColorPressed: '#D97706',

    // 错误色
    errorColor: '#F87171',
    errorColorHover: '#FCA5A5',
    errorColorPressed: '#DC2626',

    // 背景 — 浅色调
    bodyColor: '#F8FAFC',
    cardColor: '#FFFFFF',
    modalColor: '#FFFFFF',
    popoverColor: '#FFFFFF',
    tableColor: '#FFFFFF',

    // 输入控件
    inputColor: '#FFFFFF',
    inputColorDisabled: '#F1F5F9',

    // 边框
    borderColor: '#E2E8F0',
    hoverColor: '#F1F5F9',

    // 文字
    textColor1: '#334155',
    textColor2: '#64748B',
    textColor3: '#94A3B8',
    textColorDisabled: '#CBD5E1',

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
    boxShadow1: '0 1px 2px rgba(0, 0, 0, 0.05)',
    boxShadow2: '0 4px 12px rgba(0, 0, 0, 0.08)',
    boxShadow3: '0 8px 24px rgba(0, 0, 0, 0.12)',
  },

  // 按钮
  Button: {
    textColorGhostPrimary: '#1E293B',
    borderPrimary: '#1E293B',
    colorPrimary: '#1E293B',
    textColorPrimary: '#FFFFFF',
    fontWeight: '500',
    borderRadiusMedium: '6px',
  },

  // 输入框 — focus 用浅蓝交互色
  Input: {
    borderHover: '#CBD5E1',
    borderFocus: '#3B82F6',
    colorFocus: '#FFFFFF',
    textColor: '#334155',
    placeholderColor: '#94A3B8',
    borderRadius: '6px',
    heightMedium: '36px',
  },

  // 下拉选择 — focus 用浅蓝交互色
  Select: {
    peers: {
      InternalSelection: {
        borderHover: '#CBD5E1',
        borderFocus: '#3B82F6',
        heightMedium: '36px',
      },
    },
  },

  // 表格
  DataTable: {
    thColor: '#F8FAFC',
    tdColor: '#FFFFFF',
    borderColor: '#E2E8F0',
    thTextColor: '#64748B',
    tdTextColor: '#334155',
    borderRadius: '6px',
  },

  // 对话框
  Dialog: {
    contentColor: '#FFFFFF',
    headerColor: '#FFFFFF',
    borderRadius: '8px',
  },

  // 卡片
  Card: {
    color: '#FFFFFF',
    borderColor: '#E2E8F0',
    borderRadius: '8px',
    titleTextColor: '#334155',
  },

  // 折叠面板
  Collapse: {
    titleFontWeight: '500',
    titleTextColor: '#64748B',
    titleTextColorActive: '#334155',
    dividerColor: '#E2E8F0',
  },

  // 开关
  Switch: {
    railColorActive: '#3B82F6',
    buttonColor: '#FFF',
  },

  // 消息/通知
  Message: {
    color: '#FFFFFF',
    textColor: '#334155',
    borderRadius: '6px',
  },

  // 标签
  Tag: {
    borderRadius: '4px',
  },
}
