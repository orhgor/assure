(function () {
  "use strict";

  var nativeFetch = window.fetch.bind(window);

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var promise = Promise.resolve([input, init || {}]);

    if (url.indexOf("/api/") !== -1 && typeof window.getClerkToken === "function") {
      promise = window.getClerkToken().then(function (token) {
        if (!token) {
          return [input, init || {}];
        }
        var next = init ? Object.assign({}, init) : {};
        var headers = new Headers(next.headers || {});
        if (!headers.has("Authorization")) {
          headers.set("Authorization", "Bearer " + token);
        }
        next.headers = headers;
        return [input, next];
      }).catch(function () {
        return [input, init || {}];
      });
    }

    return promise.then(function (pair) {
      return nativeFetch(pair[0], pair[1]);
    });
  };
})();
