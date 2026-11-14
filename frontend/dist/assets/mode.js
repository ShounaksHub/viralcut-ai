(function(){
  const mode=()=>document.getElementById('processingMode')?.value||'BALANCED';
  const append=FormData.prototype.append;
  FormData.prototype.append=function(name,value,filename){append.call(this,name,value,filename);if(name==='file')append.call(this,'processing_mode',mode())};
  const nativeFetch=window.fetch;
  window.fetch=function(input,init){
    if(typeof input==='string'&&input==='/api/url'&&init?.body){
      try{const body=JSON.parse(init.body);body.processing_mode=mode();init={...init,body:JSON.stringify(body)}}catch{}
    }
    return nativeFetch(input,init);
  };
})();
