import {createContext,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {APPEARANCE_KEY,parseAppearance,resolveTheme,type Appearance,type LayoutPreference,type ThemePreference} from './appearance';

const AppearanceContext=createContext<{value:Appearance;resolved:'light'|'dark';setTheme:(theme:ThemePreference)=>void;setLayout:(layout:LayoutPreference)=>void}|null>(null);

export function AppearanceProvider({children}:{children:ReactNode}) {
  const [value,setValue]=useState<Appearance>(()=>{try{return parseAppearance(localStorage.getItem(APPEARANCE_KEY));}catch{return parseAppearance(null);}});
  const [systemDark,setSystemDark]=useState(()=>window.matchMedia('(prefers-color-scheme: dark)').matches);
  const resolved=resolveTheme(value.theme,systemDark);
  useEffect(()=>{
    const media=window.matchMedia('(prefers-color-scheme: dark)');
    const changed=(event:MediaQueryListEvent)=>setSystemDark(event.matches);
    media.addEventListener('change',changed);
    const sync=(event:StorageEvent)=>{if(event.key===APPEARANCE_KEY)setValue(parseAppearance(event.newValue));};
    window.addEventListener('storage',sync);
    return()=>{media.removeEventListener('change',changed);window.removeEventListener('storage',sync);};
  },[]);
  useEffect(()=>{
    document.documentElement.dataset.theme=resolved;
    document.documentElement.dataset.layout=value.layout;
    document.documentElement.style.colorScheme=resolved;
    try{localStorage.setItem(APPEARANCE_KEY,JSON.stringify(value));}catch{/* Private browsing may disable storage. The current theme still works. */}
  },[value,resolved]);
  return <AppearanceContext.Provider value={{value,resolved,setTheme:theme=>setValue(prior=>({...prior,theme})),setLayout:layout=>setValue(prior=>({...prior,layout}))}}>{children}</AppearanceContext.Provider>;
}

function ThemeIcon({dark}:{dark:boolean}) {
 return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{dark?<path d="M20.8 13A9 9 0 0 1 11 3.2 9 9 0 1 0 20.8 13Z"/>:<><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></>}</svg>;
}

export function AppearanceMenu() {
 const context=useContext(AppearanceContext);
 const root=useRef<HTMLDetailsElement>(null);
 useEffect(()=>{
  const outside=(event:PointerEvent)=>{if(root.current&&!root.current.contains(event.target as Node))root.current.open=false;};
  document.addEventListener('pointerdown',outside);
  return()=>document.removeEventListener('pointerdown',outside);
 },[]);
 if(!context)return null;
 const {value,resolved,setTheme,setLayout}=context;
 return <details className="appearance-menu" ref={root} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();root.current!.open=false;root.current?.querySelector('summary')?.focus();}}}>
  <summary aria-label="Appearance settings" title="Appearance"><ThemeIcon dark={resolved==='dark'}/><span>Appearance</span></summary>
  <div className="appearance-popover"><p>Make it yours</p><label>Theme<select aria-label="Theme" value={value.theme} onChange={event=>setTheme(event.target.value as ThemePreference)}><option value="light">Light</option><option value="dark">Dark</option><option value="system">Use device setting</option></select></label><label>Workspace layout<select aria-label="Workspace layout" value={value.layout} onChange={event=>setLayout(event.target.value as LayoutPreference)}><option value="comfortable">Comfortable</option><option value="focus">Focused</option></select></label><small>Saved on this device.</small></div>
 </details>;
}
