(function (global) {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return (meta && meta.getAttribute("content")) || "";
  }

  var originalFetch = global.fetch;
  if (typeof originalFetch === "function") {
    global.fetch = function (input, init) {
      init = init || {};
      var method = String(init.method || "GET").toUpperCase();
      if (["POST", "PUT", "PATCH", "DELETE"].indexOf(method) >= 0) {
        var token = csrfToken();
        if (token) {
          var headers = init.headers || {};
          if (headers instanceof global.Headers) {
            if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
          } else {
            headers = Object.assign({}, headers);
            if (!headers["X-CSRFToken"] && !headers["X-CSRF-TOKEN"]) {
              headers["X-CSRFToken"] = token;
            }
            init.headers = headers;
          }
        }
      }
      return originalFetch.call(global, input, init);
    };
  }

  global.AssureCsrf = { token: csrfToken };
})(window);
