export function observeBossPage(callback:()=>void){let timer=0;const observer=new MutationObserver(()=>{clearTimeout(timer);timer=window.setTimeout(callback,300)});observer.observe(document.body,{childList:true,subtree:true});return()=>observer.disconnect()}

