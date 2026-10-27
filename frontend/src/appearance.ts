export type ThemePreference = 'light' | 'dark' | 'system';
export type LayoutPreference = 'comfortable' | 'focus';
export type Appearance = {theme:ThemePreference;layout:LayoutPreference};
export const APPEARANCE_KEY = 'flowpilot.appearance';
export const DEFAULT_APPEARANCE:Appearance = {theme:'system',layout:'comfortable'};
export function parseAppearance(value:string|null):Appearance {
  try {
    const saved = JSON.parse(value || '{}');
    return {
      theme:['light','dark','system'].includes(saved?.theme) ? saved.theme : 'system',
      layout:['comfortable','focus'].includes(saved?.layout) ? saved.layout : 'comfortable',
    };
  } catch {return {...DEFAULT_APPEARANCE};}
}
export function resolveTheme(theme:ThemePreference,systemDark:boolean):'light'|'dark' {
  return theme==='system' ? (systemDark?'dark':'light') : theme;
}
