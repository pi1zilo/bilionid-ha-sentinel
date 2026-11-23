// Zabbix webhook media type. See the README for parameters and action setup.
var params = JSON.parse(value);
var request = new HttpRequest();
request.setProxy(params.proxy || "");
request.addHeader("Content-Type: application/json");
request.addHeader("X-Monitor-Secret: " + params.secret);
var recovery = /^[0-9]+$/.test(params.recovery_id || "");
var body = {
    event_id: params.event_id,
    status: recovery ? "RECOVERY" : "PROBLEM",
    name: params.name,
    host: params.host,
    severity: params.severity,
    detail: params.detail
};
request.post(params.endpoint, JSON.stringify(body));
if (request.getStatus() !== 204) {
    throw "BilioniD event endpoint returned HTTP " + request.getStatus();
}
return "{}";
