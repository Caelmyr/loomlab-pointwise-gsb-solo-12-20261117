/* LoomLab 球面光照观察模式：浏览器端 WebGL 实时着色。
   旋转与重复次数仅作用于观察层，不回写材质设置，下载的原始贴图不受影响。
   着色公式与 maps.py 的平面预览一致（sRGB↔线性、Blinn 高光、AO），
   但光源固定在画面方向，球体旋转时逐像素重新照明，不是贴一张已照亮的平面图。 */
'use strict';
const SphereView=(()=>{
  const SEG_U=128,SEG_V=96,CAM_Z=4.4,HOME={yaw:-.55,pitch:.32};
  let canvas=null,gl=null,isGL2=false,program=null,geo=null,textures=null,onError=null;
  let yaw=HOME.yaw,pitch=HOME.pitch,repeat=4,sign=1,light=[.5,.5,.707];
  let loadedKey=null,token=0;
  const U={};

  const VS='attribute vec3 aPos;attribute vec3 aTan;attribute vec3 aBit;attribute vec2 aUV;'+
    'uniform mat4 uProj;uniform mat4 uRot;'+
    'varying vec3 vN;varying vec3 vT;varying vec3 vB;varying vec3 vP;varying vec2 vUV;'+
    'void main(){vec4 w=uRot*vec4(aPos,1.);vP=w.xyz;vN=(uRot*vec4(aPos,0.)).xyz;'+
    'vT=(uRot*vec4(aTan,0.)).xyz;vB=(uRot*vec4(aBit,0.)).xyz;vUV=aUV;gl_Position=uProj*w;}';
  const FS='precision highp float;'+
    'varying vec3 vN;varying vec3 vT;varying vec3 vB;varying vec3 vP;varying vec2 vUV;'+
    'uniform sampler2D uAlbedo;uniform sampler2D uNormal;uniform sampler2D uRough;uniform sampler2D uAO;'+
    'uniform float uRepeat;uniform float uSign;uniform vec3 uLight;uniform vec3 uCam;'+
    'vec3 lin(vec3 c){return mix(c/12.92,pow((c+.055)/1.055,vec3(2.4)),step(vec3(.04045),c));}'+
    'vec3 enc(vec3 c){return mix(c*12.92,1.055*pow(max(c,vec3(0.)),vec3(1./2.4))-.055,step(vec3(.0031308),c));}'+
    'void main(){vec2 uv=vUV*uRepeat;'+
    'vec3 base=lin(texture2D(uAlbedo,uv).rgb);'+
    'vec3 n=texture2D(uNormal,uv).rgb*2.-1.;n.y*=uSign;'+
    'float rough=texture2D(uRough,uv).r;float ao=texture2D(uAO,uv).r;'+
    'vec3 nrm=normalize(normalize(vT)*n.x+normalize(vB)*n.y+normalize(vN)*n.z);'+
    'float diff=max(dot(nrm,uLight),0.);'+
    'vec3 H=normalize(uLight+normalize(uCam-vP));'+
    'float spec=pow(max(dot(nrm,H),0.),2.+80.*pow(1.-rough,2.))*(1.-rough)*.16;'+
    'vec3 lit=base*(.16+.84*diff)*ao+vec3(spec);'+
    'gl_FragColor=vec4(enc(clamp(lit,0.,1.)),1.);}';

  function geometry(){
    const pos=[],tan=[],bit=[],uv=[],idx=[];
    for(let j=0;j<=SEG_V;j++){
      const v=j/SEG_V,phi=Math.PI*(v-.5),cp=Math.cos(phi),sp=Math.sin(phi);
      for(let i=0;i<=SEG_U;i++){
        const u=i/SEG_U,th=2*Math.PI*u,st=Math.sin(th),ct=Math.cos(th);
        pos.push(st*cp,sp,ct*cp);          // 单位球面，法线即位置
        tan.push(ct,0,-st);                // 解析切线：随经度连续，接缝与两极无突变
        bit.push(-st*sp,cp,-ct*sp);
        uv.push(u+.5,v);                   // +.5 把等距圆柱接缝放到球背面
      }
    }
    for(let j=0;j<SEG_V;j++)for(let i=0;i<SEG_U;i++){
      const a=j*(SEG_U+1)+i,b=a+1,c=a+SEG_U+1,d=c+1;
      idx.push(a,b,c,b,d,c);
    }
    return {pos:new Float32Array(pos),tan:new Float32Array(tan),bit:new Float32Array(bit),
            uv:new Float32Array(uv),idx:new Uint16Array(idx)};
  }

  function perspective(fov,aspect,near,far){
    const f=1/Math.tan(fov/2),nf=1/(near-far);
    return new Float32Array([f/aspect,0,0,0,0,f,0,0,0,0,(far+near)*nf,-1,0,0,2*far*near*nf,0]);
  }
  function matMul(a,b){
    const o=new Float32Array(16);
    for(let c=0;c<4;c++)for(let r=0;r<4;r++){let s=0;for(let k=0;k<4;k++)s+=a[k*4+r]*b[c*4+k];o[c*4+r]=s;}
    return o;
  }
  function viewMatrix(){return new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,-CAM_Z,1]);}
  function rotation(){
    const cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(pitch),sx=Math.sin(pitch);
    return new Float32Array([cy,sx*sy,-cx*sy,0,0,cx,sx,0,sy,-sx*cy,cx*cy,0,0,0,0,1]);
  }

  function shader(type,src){
    const s=gl.createShader(type);
    gl.shaderSource(s,src);
    gl.compileShader(s);
    if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('着色器编译失败：'+gl.getShaderInfoLog(s));
    return s;
  }

  function buildBuffers(){
    const data=geometry();
    geo={count:data.idx.length,attribs:[]};
    for(const [name,arr,size] of [['aPos',data.pos,3],['aTan',data.tan,3],['aBit',data.bit,3],['aUV',data.uv,2]]){
      const buf=gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER,buf);
      gl.bufferData(gl.ARRAY_BUFFER,arr,gl.STATIC_DRAW);
      geo.attribs.push({loc:gl.getAttribLocation(program,name),buf,size});
    }
    geo.index=gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,geo.index);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,data.idx,gl.STATIC_DRAW);
  }

  function pot(x){let p=64;while(p<x&&p<1024)p*=2;return p;}
  function upload(image,unit){
    let src=image;
    if(!isGL2&&((image.width&(image.width-1))||(image.height&(image.height-1)))){
      const c=document.createElement('canvas');   // WebGL1 不支持非2次幂重复采样，仅观察层重采样
      c.width=pot(image.width);c.height=pot(image.height);
      c.getContext('2d').drawImage(image,0,0,c.width,c.height);
      src=c;
    }
    const t=gl.createTexture();
    gl.activeTexture(gl.TEXTURE0+unit);
    gl.bindTexture(gl.TEXTURE_2D,t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,src);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    const ext=gl.getExtension('EXT_texture_filter_anisotropic');
    if(ext)gl.texParameterf(gl.TEXTURE_2D,ext.TEXTURE_MAX_ANISOTROPY_EXT,Math.min(4,gl.getParameter(ext.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
    return t;
  }

  function draw(){
    if(!gl||!textures||!canvas||canvas.hidden)return;
    const dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(1,Math.round(canvas.clientWidth*dpr)),h=Math.max(1,Math.round(canvas.clientHeight*dpr));
    if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}
    gl.viewport(0,0,w,h);
    gl.clearColor(0,0,0,0);
    gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.useProgram(program);
    gl.uniformMatrix4fv(U.uProj,false,matMul(perspective(Math.PI/6,w/h,.1,10),viewMatrix()));
    gl.uniformMatrix4fv(U.uRot,false,rotation());
    gl.uniform1f(U.uRepeat,repeat);
    gl.uniform1f(U.uSign,sign);
    gl.uniform3fv(U.uLight,light);
    gl.uniform3fv(U.uCam,[0,0,CAM_Z]);
    for(const a of geo.attribs){
      gl.bindBuffer(gl.ARRAY_BUFFER,a.buf);
      gl.enableVertexAttribArray(a.loc);
      gl.vertexAttribPointer(a.loc,a.size,gl.FLOAT,false,0,0);
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,geo.index);
    gl.drawElements(gl.TRIANGLES,geo.count,gl.UNSIGNED_SHORT,0);
  }

  function loadImage(url){
    return new Promise((resolve,reject)=>{
      const im=new Image();
      im.onload=()=>resolve(im);
      im.onerror=()=>reject(Error('材质贴图载入失败'));
      im.src=url;
    });
  }

  function bindPointer(){
    let drag=null;
    canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener('pointermove',e=>{
      if(!drag)return;
      yaw+=(e.clientX-drag.x)*.008;
      pitch=Math.max(-1.45,Math.min(1.45,pitch+(e.clientY-drag.y)*.008));
      drag={x:e.clientX,y:e.clientY};
      draw();
    });
    const end=()=>{drag=null;};
    canvas.addEventListener('pointerup',end);
    canvas.addEventListener('pointercancel',end);
  }

  function attach(el,errorCallback){
    canvas=el;
    onError=errorCallback||null;
    gl=el.getContext('webgl2',{alpha:true,antialias:true});
    isGL2=!!gl;
    if(!gl)gl=el.getContext('webgl',{alpha:true,antialias:true});
    if(!gl)return false;
    try{
      program=gl.createProgram();
      gl.attachShader(program,shader(gl.VERTEX_SHADER,VS));
      gl.attachShader(program,shader(gl.FRAGMENT_SHADER,FS));
      gl.linkProgram(program);
      if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('着色器链接失败：'+gl.getProgramInfoLog(program));
    }catch(error){gl=null;if(onError)onError(error);return false;}
    for(const n of ['uProj','uRot','uRepeat','uSign','uLight','uCam','uAlbedo','uNormal','uRough','uAO'])
      U[n]=gl.getUniformLocation(program,n);
    buildBuffers();
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.CULL_FACE);
    gl.cullFace(gl.BACK);
    gl.useProgram(program);
    gl.uniform1i(U.uAlbedo,0);
    gl.uniform1i(U.uNormal,1);
    gl.uniform1i(U.uRough,2);
    gl.uniform1i(U.uAO,3);
    bindPointer();
    window.addEventListener('resize',draw);
    return true;
  }

  function setMaterial(key,urls){
    if(!gl)return;
    if(key===loadedKey&&textures){draw();return;}
    const my=++token;
    Promise.all(urls.map(loadImage)).then(images=>{
      if(my!==token||!gl)return;
      if(textures)for(const t of textures)gl.deleteTexture(t);
      textures=[upload(images[0],0),upload(images[1],1),upload(images[2],2),upload(images[3],3)];
      loadedKey=key;
      draw();
    }).catch(error=>{if(my===token&&onError)onError(error);});
  }

  return {
    attach,
    setMaterial,
    setLight(v){light=v;draw();},
    setNormalSign(s){sign=s;draw();},
    setRepeat(n){repeat=n;draw();},
    reset(){yaw=HOME.yaw;pitch=HOME.pitch;draw();},
    draw
  };
})();
