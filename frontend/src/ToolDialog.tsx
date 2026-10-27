import {useEffect,useRef,type ReactNode} from 'react';
import {Icon} from './Icon';
export function ToolDialog({title,onClose,children,wide=false}:{title:string;onClose:()=>void;children:ReactNode;wide?:boolean}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const dialog=ref.current;dialog?.showModal();return()=>dialog?.close();},[]);
 return <dialog className={'tool-dialog '+(wide?'wide':'')} ref={ref} aria-label={title} onCancel={onClose} onClick={e=>{if(e.target===ref.current){const r=ref.current.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)onClose();}}}><header><h2>{title}</h2><button aria-label="Close dialog" onClick={onClose}><Icon name="close"/></button></header><div className="tool-dialog-body">{children}</div></dialog>;
}
