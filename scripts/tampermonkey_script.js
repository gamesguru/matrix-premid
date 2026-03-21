// ==UserScript==
// @name         Matrix Presence Updater
// @namespace    http://tampermonkey.net/
// @version      1.1
// @description  Sends current tab info to local Python server for Matrix Status
// @author       You
// @match        *://*.youtube.com/*
// @match        *://music.youtube.com/*
// @match        *://*.github.com/*
// @match        *://*.wikipedia.org/*
// @grant        GM_xmlhttpRequest
// ==/UserScript==

(function () {
  "use strict";

  let lastActivity = "";

  setInterval(() => {
    let title = document.title;
    let activity = "";
    let host = window.location.hostname;

    // Parse title based on the current site
    if (host === "music.youtube.com") {
      // Cleans notification badges like "(3)" and the site name
      activity =
        "Listening to: " +
        title.replace(/^\(\d+\)\s*/, "").replace(" - YouTube Music", "");
    } else if (host.includes("youtube.com")) {
      activity =
        "Watching: " +
        title.replace(/^\(\d+\)\s*/, "").replace(" - YouTube", "");
    } else if (host.includes("github.com")) {
      activity =
        "On GitHub: " + title.replace(" · GitHub", "").replace("GitHub - ", "");
    } else if (host.includes("wikipedia.org")) {
      activity = "Reading: " + title.replace(" - Wikipedia", "");
    }

    // Only send a request if the parsed activity changed
    if (activity && activity !== lastActivity) {
      lastActivity = activity;

      GM_xmlhttpRequest({
        method: "POST",
        url: "http://localhost:8080/update",
        data: JSON.stringify({ activity: activity }),
        headers: {
          "Content-Type": "application/json",
        },
        onload: function (response) {
          // console.log("Matrix updated: ", response.responseText);
        },
        onerror: function (error) {
          console.error(
            "Failed to update Matrix presence. Is the Python server running?",
            error,
          );
        },
      });
    }
  }, 5000);
})();
