/* Carton viewers: interactive 3D scene (three.js), per-layer top-view SVG maps and orientation icons.
 *
 * Carton coordinates (mm):  X = carton LENGTH (left->right), Y = carton WIDTH (front->back), Z = HEIGHT (up).
 * perm = [a,b,c]  ->  box axis index a lies along X, b along Y, c along Z  (0=L, 1=W, 2=H of the inner box).
 */
const PERMS = [[0,1,2],[0,2,1],[1,0,2],[1,2,0],[2,0,1],[2,1,0]];
const AXCOL = {L:'#d64541', W:'#1f9d57', H:'#2f6fdd'};
const AXN = ['L','W','H'];

function shade(hex, f){
  let n = parseInt(hex.slice(1),16), r=(n>>16)&255, g=(n>>8)&255, b=n&255;
  r=Math.max(0,Math.min(255,Math.round(r*f))); g=Math.max(0,Math.min(255,Math.round(g*f))); b=Math.max(0,Math.min(255,Math.round(b*f)));
  return '#'+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

/* where (carton axis index 0/1/2) does box axis `ax` (0=L,1=W,2=H) point */
function carAxisOf(perm, ax){ return PERMS[perm].indexOf(ax); }

/* ---------------------------------------------------------------- orientation icon (isometric SVG) */
function isoIcon(ext, perm, color, size){
  size = size || 110;
  const mx = Math.max(ext.x, ext.y, ext.z);
  const k = (size*0.52)/mx, c30 = Math.cos(Math.PI/6), s30 = 0.5;
  const P = (x,y,z)=>[(x-y)*c30*k, (x+y)*s30*k - z*k];
  const X=ext.x, Y=ext.y, Z=ext.z;
  const pts = {
    t:[P(0,0,Z),P(X,0,Z),P(X,Y,Z),P(0,Y,Z)],
    r:[P(X,0,0),P(X,Y,0),P(X,Y,Z),P(X,0,Z)],
    f:[P(0,Y,0),P(X,Y,0),P(X,Y,Z),P(0,Y,Z)]
  };
  let all=[].concat(pts.t,pts.r,pts.f), minx=1e9,maxx=-1e9,miny=1e9,maxy=-1e9;
  all.forEach(p=>{minx=Math.min(minx,p[0]);maxx=Math.max(maxx,p[0]);miny=Math.min(miny,p[1]);maxy=Math.max(maxy,p[1]);});
  const pad=22, W=maxx-minx+pad*2, H=maxy-miny+pad*2, ox=-minx+pad, oy=-miny+pad;
  const poly=(a,fill)=>`<polygon points="${a.map(p=>(p[0]+ox).toFixed(1)+','+(p[1]+oy).toFixed(1)).join(' ')}" fill="${fill}" stroke="#0007" stroke-width="1"/>`;
  // which visible face is the box's H+ face (its lid)?
  const hAx = carAxisOf(perm, 2);
  const lid = hAx===2 ? 't' : hAx===0 ? 'r' : 'f';
  const f = (id,base)=> id===lid ? shade(color,base*0.62) : shade(color,base);
  let s = `<svg viewBox="0 0 ${W.toFixed(0)} ${H.toFixed(0)}" width="${W.toFixed(0)}" height="${H.toFixed(0)}">`;
  s += poly(pts.f,f('f',0.78))+poly(pts.r,f('r',0.9))+poly(pts.t,f('t',1.08));
  // edge labels
  const lbl=(x,y,t,col)=>`<text x="${(x+ox).toFixed(1)}" y="${(y+oy).toFixed(1)}" font-size="11" font-weight="700" fill="${col}" text-anchor="middle" style="paint-order:stroke;stroke:#fff;stroke-width:3px">${t}</text>`;
  const axX = AXN[PERMS[perm][0]], axY = AXN[PERMS[perm][1]], axZ = AXN[PERMS[perm][2]];
  let m = P(X/2,Y,0); s += lbl(m[0]-10, m[1]+14, axX, AXCOL[axX]);          // along carton length: front-right bottom edge
  m = P(0,Y/2,0);     s += lbl(m[0]-12, m[1]+12, axY, AXCOL[axY]);          // along carton width: left-bottom edge
  m = P(X,Y,Z/2);     s += lbl(m[0]+13, m[1]+4,  axZ, AXCOL[axZ]);          // vertical edge
  s += '</svg>';
  return s;
}

/* ---------------------------------------------------------------- top view layer map (SVG) */
function layerSVG(carton, layer, prevItems, colorOf, badgeOf, width){
  width = width || 330;
  const X=carton.l, Y=carton.w;
  const k = (width-16)/X, H = Y*k+16;
  const sx = x=>8+x*k, sy = (y,h)=>8+(Y-y-h)*k;
  let s = `<svg viewBox="0 0 ${width} ${H.toFixed(0)}" xmlns="http://www.w3.org/2000/svg">`;
  s += `<rect x="8" y="8" width="${(X*k).toFixed(1)}" height="${(Y*k).toFixed(1)}" fill="none" stroke="#778" stroke-width="2"/>`;
  // ghost previous layers
  (prevItems||[]).forEach(it=>{
    for(let iy=0;iy<it.ny;iy++)for(let ix=0;ix<it.nx;ix++){
      const x=it.x+ix*it.dx, y=it.y+iy*it.dy;
      s+=`<rect x="${sx(x).toFixed(1)}" y="${sy(y,it.dy).toFixed(1)}" width="${(it.dx*k).toFixed(1)}" height="${(it.dy*k).toFixed(1)}" fill="#8888" fill-opacity=".16" stroke="#8886" stroke-width=".6"/>`;
    }
  });
  layer.items.forEach(it=>{
    const col = colorOf(it.sku), perm = it.perm, p = PERMS[perm];
    const lAx = p.indexOf(0), hAx = p.indexOf(2);
    for(let iy=0;iy<it.ny;iy++)for(let ix=0;ix<it.nx;ix++){
      const x=it.x+ix*it.dx, y=it.y+iy*it.dy;
      const rx=sx(x), ry=sy(y,it.dy), rw=it.dx*k, rh=it.dy*k;
      s+=`<rect x="${rx.toFixed(1)}" y="${ry.toFixed(1)}" width="${rw.toFixed(1)}" height="${rh.toFixed(1)}" fill="${col}" stroke="#fffc" stroke-width="1"/>`;
      // lid bar when the box lies on its side (H horizontal): thick edge on the +H side
      if(hAx===0) s+=`<rect x="${(rx+rw-3).toFixed(1)}" y="${ry.toFixed(1)}" width="3" height="${rh.toFixed(1)}" fill="#111"/>`;
      if(hAx===1) s+=`<rect x="${rx.toFixed(1)}" y="${ry.toFixed(1)}" width="${rw.toFixed(1)}" height="3" fill="#111"/>`;
      // arrow along L
      const cx=rx+rw/2, cy=ry+rh/2;
      if(lAx===0 && rw>14){ const a=Math.min(rw*0.32, 14); s+=arrow(cx-a,cy,cx+a,cy); }
      else if(lAx===1 && rh>14){ const a=Math.min(rh*0.32, 14); s+=arrow(cx,cy+a,cx,cy-a); }
      else if(lAx===2){ s+=`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3.2" fill="#fff" stroke="#111" stroke-width="1"/><circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="1" fill="#111"/>`; }
    }
    // badge on first box of the item
    const b = badgeOf(it.sku);
    const bw = it.nx*it.dx*k, bh = it.ny*it.dy*k;
    const bx = sx(it.x)+bw/2, by = sy(it.y, it.ny*it.dy)+bh/2;
    s+=`<circle cx="${bx.toFixed(1)}" cy="${by.toFixed(1)}" r="9" fill="#111" fill-opacity=".78"/><text x="${bx.toFixed(1)}" y="${(by+3.6).toFixed(1)}" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">${b}</text>`;
  });
  s+=`<text x="${(width/2).toFixed(0)}" y="${(H-1).toFixed(0)}" font-size="9" fill="#889" text-anchor="middle">&#9660; FRONT (worker side)</text>`;
  s+='</svg>';
  return s;
}
function arrow(x1,y1,x2,y2){
  const a=Math.atan2(y2-y1,x2-x1), h=4.5;
  const p1=[x2-h*Math.cos(a-0.5),y2-h*Math.sin(a-0.5)], p2=[x2-h*Math.cos(a+0.5),y2-h*Math.sin(a+0.5)];
  return `<g stroke="#111" stroke-width="1.6" fill="#111"><line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/><polygon points="${x2.toFixed(1)},${y2.toFixed(1)} ${p1[0].toFixed(1)},${p1[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}" stroke="none"/></g>`;
}

/* ---------------------------------------------------------------- 3D scene */
class CartonScene {
  constructor(container, opts){
    opts = opts||{};
    this.container = container;
    this.W = container.clientWidth || 640; this.H = opts.height || 460;
    this.renderer = new THREE.WebGLRenderer({antialias:true, alpha:true, preserveDrawingBuffer:true});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1, 2));
    this.renderer.setSize(this.W, this.H);
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, this.W/this.H, 0.1, 500);
    this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.group = new THREE.Group(); this.scene.add(this.group);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.72));
    const d = new THREE.DirectionalLight(0xffffff, 0.75); d.position.set(4,9,6); this.scene.add(d);
    const d2 = new THREE.DirectionalLight(0xffffff, 0.25); d2.position.set(-6,3,-4); this.scene.add(d2);
    this.boxes = []; this.running = true;
    this.controls.addEventListener('change', ()=>this.render());
    this._ro = new ResizeObserver(()=>this.resize()); this._ro.observe(container);
  }
  resize(){
    const w = this.container.clientWidth; if(!w || Math.abs(w-this.W)<2) return;
    this.W = w; this.renderer.setSize(this.W, this.H); this.camera.aspect = this.W/this.H; this.camera.updateProjectionMatrix(); this.render();
  }
  clear(){
    while(this.group.children.length){
      const o = this.group.children.pop();
      o.traverse(n=>{ if(n.geometry) n.geometry.dispose(); if(n.material){ (Array.isArray(n.material)?n.material:[n.material]).forEach(m=>{ if(m.map) m.map.dispose(); m.dispose(); }); }});
    }
    this.boxes = [];
  }
  static sprite(text, bg){
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const g = c.getContext('2d'); g.fillStyle = bg||'#111'; g.globalAlpha = .88; g.beginPath(); g.arc(32,32,28,0,7); g.fill();
    g.globalAlpha = 1; g.fillStyle='#fff'; g.font='bold 34px sans-serif'; g.textAlign='center'; g.textBaseline='middle'; g.fillText(text,32,34);
    const t = new THREE.CanvasTexture(c); const m = new THREE.SpriteMaterial({map:t, depthTest:true, transparent:true});
    return new THREE.Sprite(m);
  }
  static textSprite(text, color){
    const c = document.createElement('canvas'); c.width = 384; c.height = 64;
    const g = c.getContext('2d'); g.font='bold 30px sans-serif'; g.textAlign='center'; g.textBaseline='middle';
    g.lineWidth=6; g.strokeStyle='#fff'; g.strokeText(text,192,34); g.fillStyle=color||'#223'; g.fillText(text,192,34);
    const t = new THREE.CanvasTexture(c); const m = new THREE.SpriteMaterial({map:t, depthTest:false, transparent:true});
    return new THREE.Sprite(m);
  }
  /* carton: {l,w,h} mm; data: {blocks, layers}; colorOf(sku)->hex; badgeOf(sku)->string */
  load(carton, data, colorOf, badgeOf, opts){
    opts = opts||{};
    this.clear();
    const S = 0.01;     // mm -> scene units
    this.S = S; this.carton = carton; this.data = data;
    const X = carton.l*S, Y = carton.w*S, Z = carton.h*S;
    this.dims = [X,Y,Z];
    const T = (x,y,z)=> new THREE.Vector3(x*S, z*S, y*S);       // carton -> three coords
    // layer lookup for each box
    const layerOf = {};
    data.layers.forEach(L=>L.items.forEach(it=>{
      for(let iy=0;iy<it.ny;iy++)for(let ix=0;ix<it.nx;ix++){
        layerOf[[it.block,Math.round(it.x+ix*it.dx),Math.round(it.y+iy*it.dy),Math.round(it.z)].join('_')] = L.no;
      }
    }));
    data.blocks.forEach((b,bi)=>{
      const col = colorOf(b.sku), p = PERMS[b.perm];
      const hAxis = p.indexOf(2), lAxis = p.indexOf(0);   // carton axes of box H and L
      for(let iz=0;iz<b.nz;iz++)for(let iy=0;iy<b.ny;iy++)for(let ix=0;ix<b.nx;ix++){
        const x=b.x+ix*b.dx, y=b.y+iy*b.dy, z=b.z+iz*b.dz;
        const layer = layerOf[[bi,Math.round(x),Math.round(y),Math.round(z)].join('_')] || 1;
        const gp = new THREE.Group();
        const geo = new THREE.BoxGeometry(b.dx*S, b.dz*S, b.dy*S);
        const mat = new THREE.MeshLambertMaterial({color: col, transparent:true, opacity:1});
        const mesh = new THREE.Mesh(geo, mat);
        const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({color:0x1b2330, transparent:true, opacity:.75}));
        gp.add(mesh); gp.add(edges);
        // lid face (box H+ face) darker overlay + L arrow
        const ext = [b.dx, b.dy, b.dz];       // extents along carton axes 0,1,2
        const half = ext.map(v=>v*S/2);
        const eps = 0.004;
        // basis in three coords: carton axis i -> three axis idx [0,2,1]
        const t3 = [0,2,1];
        const nrm = new THREE.Vector3(); const hv = [0,0,0]; hv[t3[hAxis]] = 1; nrm.set(hv[0],hv[1],hv[2]);
        const lv = new THREE.Vector3(); const la=[0,0,0]; la[t3[lAxis]] = 1; lv.set(la[0],la[1],la[2]);
        const wAxis = p.indexOf(1); const wv = new THREE.Vector3(); const wa=[0,0,0]; wa[t3[wAxis]]=1; wv.set(wa[0],wa[1],wa[2]);
        const fw = ext[lAxis]*S, fh = ext[wAxis]*S;
        const plane = new THREE.Mesh(new THREE.PlaneGeometry(fw*0.999, fh*0.999),
              new THREE.MeshBasicMaterial({color: 0x000000, transparent:true, opacity:.22, side:THREE.DoubleSide, depthWrite:false}));
        // arrow shape in (u=L, v=W) plane
        const sh = new THREE.Shape(); const a = fw*0.34, hw = Math.min(fh*0.07, 0.035), ah = Math.min(fh*0.22, 0.09);
        sh.moveTo(-a,-hw); sh.lineTo(a-ah,-hw); sh.lineTo(a-ah,-ah); sh.lineTo(a,0); sh.lineTo(a-ah,ah); sh.lineTo(a-ah,hw); sh.lineTo(-a,hw); sh.closePath();
        const arrow = new THREE.Mesh(new THREE.ShapeGeometry(sh), new THREE.MeshBasicMaterial({color:0xffe14d, side:THREE.DoubleSide, depthWrite:false}));
        const m4 = new THREE.Matrix4(); m4.makeBasis(lv, wv, nrm);
        [plane, arrow].forEach((o,i)=>{
          o.applyMatrix4(m4);
          o.position.copy(nrm.clone().multiplyScalar(half[hAxis]+eps*(i+1)));
          o.userData.mark = true;
          gp.add(o);
        });
        gp.position.copy(T(x+b.dx/2, y+b.dy/2, z+b.dz/2));
        gp.userData = {layer, block:bi, base: gp.position.clone(), mat};
        this.group.add(gp); this.boxes.push(gp);
      }
      // block badge sprite on the top of the block
      const sp = CartonScene.sprite(badgeOf(b.sku), '#101820');
      sp.scale.set(0.16,0.16,1);
      const topz = b.z + b.nz*b.dz;
      sp.position.copy(T(b.x + b.nx*b.dx/2, b.y + b.ny*b.dy/2, topz)); sp.position.y += 0.1;
      sp.userData = {layer: Math.max(...data.layers.filter(L=>L.items.some(it=>it.block===bi)).map(L=>L.no)), badge:true, base: sp.position.clone()};
      this.group.add(sp); this.boxes.push(sp);
    });
    // carton shell
    const cg = new THREE.BoxGeometry(X,Z,Y);
    const shell = new THREE.Mesh(cg, new THREE.MeshBasicMaterial({color:0xb08a5a, transparent:true, opacity:.10, side:THREE.BackSide, depthWrite:false}));
    shell.position.set(X/2, Z/2, Y/2); this.group.add(shell);
    const wire = new THREE.LineSegments(new THREE.EdgesGeometry(cg), new THREE.LineBasicMaterial({color:0x7a5a2e}));
    wire.position.copy(shell.position); this.group.add(wire);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(X,Y), new THREE.MeshBasicMaterial({color:0xcaa874, transparent:true, opacity:.35, side:THREE.DoubleSide}));
    floor.rotation.x = -Math.PI/2; floor.position.set(X/2,-0.002,Y/2); this.group.add(floor);
    // dimension labels
    const l1 = CartonScene.textSprite('LENGTH '+carton.l.toFixed(0)+' mm  →'); l1.scale.set(2.2,0.37,1); l1.position.set(X/2,-0.15,Y+0.25); this.group.add(l1);
    const l2 = CartonScene.textSprite('WIDTH '+carton.w.toFixed(0)+' mm'); l2.scale.set(2.0,0.33,1); l2.position.set(X+0.55,-0.15,Y/2); this.group.add(l2);
    const l3 = CartonScene.textSprite('HEIGHT '+carton.h.toFixed(0)+' mm'); l3.scale.set(2.1,0.35,1); l3.position.set(-0.55,Z/2,Y+0.0); this.group.add(l3);
    this.maxLayer = data.layers.length ? Math.max(...data.layers.map(l=>l.no)) : 1;
    // camera
    const c = new THREE.Vector3(X/2, Z/2, Y/2);
    const r = Math.max(X,Y,Z)*2.35;
    this.camera.position.set(c.x + r*0.78, c.y + r*0.7, c.z + r*0.95);
    this.controls.target.copy(c); this.controls.update();
    this.opts = {upTo:this.maxLayer, ghost:true, explode:false, marks:true};
    this.apply();
  }
  setOpts(o){ Object.assign(this.opts,o); this.apply(); }
  apply(){
    const o = this.opts, gap = o.explode ? 0.22 : 0;
    this.boxes.forEach(g=>{
      const L = g.userData.layer;
      g.visible = L <= o.upTo;
      if(g.userData.badge){ g.position.y = g.userData.base.y + (L-1)*gap; return; }
      g.position.y = g.userData.base.y + (L-1)*gap;
      const cur = (L === o.upTo);
      g.userData.mat.opacity = (o.ghost && !cur && o.upTo < this.maxLayer) ? 0.35 : 1;
      g.children.forEach(ch=>{ if(ch.userData && ch.userData.mark) ch.visible = o.marks; });
    });
    this.render();
  }
  render(){ this.renderer.render(this.scene, this.camera); }
  snapshot(){ this.render(); return this.renderer.domElement.toDataURL('image/png'); }
  dispose(){ this._ro.disconnect(); this.clear(); this.renderer.dispose(); if(this.renderer.domElement.parentNode) this.renderer.domElement.parentNode.removeChild(this.renderer.domElement); }
}

/* offscreen snapshot used for printing */
let _snapHost = null;
function snapshotCarton(carton, data, colorOf, badgeOf){
  if(!_snapHost){ _snapHost = document.createElement('div'); _snapHost.style.cssText='position:fixed;left:-9999px;top:0;width:760px;height:480px'; document.body.appendChild(_snapHost); }
  const sc = new CartonScene(_snapHost, {height:480});
  sc.W = 760; sc.renderer.setSize(760,480); sc.camera.aspect = 760/480; sc.camera.updateProjectionMatrix();
  sc.load(carton, data, colorOf, badgeOf);
  const url = sc.snapshot(); sc.dispose(); return url;
}
