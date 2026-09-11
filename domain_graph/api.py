"""Framework-neutral HTTP adapter; authentication is supplied by trusted middleware."""
import json
from .query_contract import QueryApiError, parse_request, request_id, MAX_BODY, Principal
class GraphQueryApi:
    def __init__(self, service, principal_provider): self.service=service; self.principal_provider=principal_provider
    def handle(self, method, headers, body):
        rid=request_id(headers.get("X-Request-Id")); principal=None
        try:
            if method!="POST": raise QueryApiError("invalid_request",400)
            if headers.get("Content-Type","").split(";",1)[0].lower()!="application/json": raise QueryApiError("unsupported_media_type",415,"unsupported media type")
            if not isinstance(body,(bytes,bytearray)) or len(body)>MAX_BODY: raise QueryApiError("invalid_request",400)
            principal=self.principal_provider(); req=parse_request(json.loads(body)); result=self.service.trace(req,principal,rid); return self._response(200,result,rid)
        except QueryApiError as exc: return self._response(exc.status,{"error":{"code":exc.code,"message":str(exc)} ,"meta":{"requestId":rid}},rid)
        except (ValueError,TypeError,json.JSONDecodeError): return self._response(400,{"error":{"code":"invalid_request","message":"request is invalid"},"meta":{"requestId":rid}},rid)
        except Exception: return self._response(500,{"error":{"code":"internal_error","message":"internal error"},"meta":{"requestId":rid}},rid)
    @staticmethod
    def _response(status,payload,rid): return status,{"Content-Type":"application/json; charset=utf-8","Cache-Control":"no-store","X-Request-Id":rid},json.dumps(payload,ensure_ascii=False,separators=(",",":"))
