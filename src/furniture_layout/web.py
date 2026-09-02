from __future__ import annotations

import argparse, json, os, struct, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .optimizer import NoValidLayoutError
from .polygon_engine import generate_polygon_layouts, validate_layout
from .extraction import associate_openings, bounded_snap_segments, extraction_contract
from .project import ProjectStore, ProjectValidationError, calibrate_scale, import_legacy, legacy_layouts_to_project, new_project, validate_project
from .service import generate_layouts

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT, SAMPLE_FILE, PRESETS_FILE = PROJECT_ROOT/"web", PROJECT_ROOT/"examples"/"living_room.json", PROJECT_ROOT/"examples"/"room_presets.json"
STORE, MAX_BODY = ProjectStore(PROJECT_ROOT/"output"/"projects"), 5_000_000

def _png(width:int,height:int,placements:list[dict])->bytes:
    width=max(100,min(2000,width));height=max(100,min(2000,height));pixels=bytearray([248,250,252,255]*width*height)
    colours=((14,165,233,255),(20,184,166,255),(139,92,246,255),(245,158,11,255))
    for i,p in enumerate(placements):
        x=max(0,min(width-1,int(p.get("x",0)*20)));y=max(0,min(height-1,int(p.get("y",0)*20)));w=max(1,int(p.get("width",1)*20));h=max(1,int(p.get("depth",1)*20));colour=colours[i%len(colours)]
        for py in range(y,min(height,y+h)):
            for px in range(x,min(width,x+w)):pixels[(py*width+px)*4:(py*width+px+1)*4]=bytes(colour)
    raw=b"".join(b"\0"+pixels[y*width*4:(y+1)*width*4] for y in range(height));chunk=lambda kind,data:struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,6,0,0,0))+chunk(b"IDAT",zlib.compress(raw,9))+chunk(b"IEND",b"")

def inspect_model()->dict:
    configured=os.environ.get("FLOORPLAN_MODEL_PATH")
    if not configured:return {"available":False,"code":"missing-model","error":"Set FLOORPLAN_MODEL_PATH to a trusted server-side best.pt file. Client paths are not accepted."}
    path=Path(configured).resolve()
    if not path.is_file() or path.suffix.lower()!=".pt":return {"available":False,"code":"missing-model","error":f"Configured model does not exist or is not a .pt file: {path}"}
    try:
        from ultralytics import YOLO
        model=YOLO(str(path));return {"available":True,"path":str(path),"names":model.names,"modelCachedByUltralytics":True}
    except Exception as error:return {"available":False,"code":"model-load-failed","error":f"Checkpoint could not be loaded: {error}"}

class DemoHandler(BaseHTTPRequestHandler):
    def _send(self,status:int,body:bytes,content_type:str,disposition:str|None=None)->None:
        self.send_response(status);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store")
        if disposition:self.send_header("Content-Disposition",disposition)
        self.end_headers();self.wfile.write(body)
    def _json(self,status:int,payload:dict)->None:self._send(status,json.dumps(payload).encode(),"application/json; charset=utf-8")
    def _body(self)->dict:
        length=int(self.headers.get("Content-Length","0"))
        if length<1 or length>MAX_BODY:raise ProjectValidationError(f"Request body must be between 1 byte and {MAX_BODY} bytes")
        value=json.loads(self.rfile.read(length))
        if not isinstance(value,dict):raise ProjectValidationError("JSON body must be an object")
        return value
    def do_GET(self)->None:
        path=urlparse(self.path).path
        try:
            if path=="/api/sample":return self._send(200,SAMPLE_FILE.read_bytes(),"application/json")
            if path=="/api/presets":return self._send(200,PRESETS_FILE.read_bytes(),"application/json")
            if path=="/api/model":return self._json(200,inspect_model())
            if path.startswith("/api/projects/"):return self._json(200,STORE.load(path.rsplit("/",1)[-1]))
            if path in ("/","/index.html"):return self._send(200,(WEB_ROOT/"layouts-only.html").read_bytes(),"text/html; charset=utf-8")
            if path=="/layouts-only.html":return self._send(200,(WEB_ROOT/"layouts-only.html").read_bytes(),"text/html; charset=utf-8")
            self._json(404,{"error":"Not found"})
        except (ValueError,ProjectValidationError) as error:self._json(400,{"error":str(error)})
    def do_POST(self)->None:
        path=urlparse(self.path).path
        try:
            data=self._body()
            if path=="/api/layouts":result=generate_layouts(data)
            elif path=="/api/projects/new":result=new_project(data.get("image"))
            elif path=="/api/import/legacy":result=import_legacy(data.get("data",data),data.get("sourceName","legacy.json"))
            elif path=="/api/import/layout-project":result=legacy_layouts_to_project(data.get("data",data),data.get("sourceName","legacy.json"))
            elif path=="/api/calibrate":result=calibrate_scale(data["pointA"],data["pointB"],data["knownLength"],data["unit"])
            elif path=="/api/layouts/generate":result=generate_polygon_layouts(data)
            elif path=="/api/layouts/validate":result=validate_layout(data)
            elif path=="/api/geometry/snap":result={"segments":bounded_snap_segments(data.get("segments",[]),float(data.get("tolerancePx",4)))}
            elif path=="/api/openings/associate":result={"openings":associate_openings(data.get("openings",[]),data.get("walls",[]),data.get("rooms",[]),float(data.get("tolerancePx",8)))}
            elif path=="/api/extraction/review":result=extraction_contract(data)
            elif path=="/api/projects/save":STORE.save(data);result={"saved":True,"id":data["id"]}
            elif path=="/api/export/json":validate_project(data);return self._send(200,json.dumps(data,indent=2).encode(),"application/json",'attachment; filename="floorplan-project.json"')
            elif path=="/api/export/png":return self._send(200,_png(int(data.get("width",1000)),int(data.get("height",700)),data.get("placements",[])),"image/png",'attachment; filename="floorplan-layout.png"')
            elif path in {"/api/detect","/api/rooms/extract"}:
                model=inspect_model()
                if not model["available"]:raise ProjectValidationError(model["error"])
                raise ProjectValidationError("Detection/extraction requires the original image and configured post-processing; no result was fabricated")
            else:return self._json(404,{"error":"Not found"})
            self._json(200,result)
        except (json.JSONDecodeError,KeyError,TypeError,ValueError,NoValidLayoutError,ProjectValidationError) as error:self._json(400,{"error":str(error)})
    def log_message(self,format:str,*args:object)->None:print(f"{self.address_string()} - {format % args}")

def main()->int:
    parser=argparse.ArgumentParser(description="Run the FloorPlanAI 2D workspace");parser.add_argument("--host",default="127.0.0.1");parser.add_argument("--port",type=int,default=8090);args=parser.parse_args();server=ThreadingHTTPServer((args.host,args.port),DemoHandler);print(f"FloorPlanAI: http://{args.host}:{args.port}",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return 0
if __name__=="__main__":raise SystemExit(main())
