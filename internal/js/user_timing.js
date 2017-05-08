(function() {
var m = [];
try {
  var marks = window.performance.getEntriesByType("mark");
  if (marks.length) {
    for (var i = 0; i < marks.length; i++)
      m.push({"type": "mark",
              "entryType": marks[i].entryType,
              "name": marks[i].name,
              "startTime": marks[i].startTime});
  }
} catch(e) {};
try {
  var measures = window.performance.getEntriesByType("measure");
  if (measures.length) {
    for (var i = 0; i < measures.length; i++)
      m.push({"type": "measure",
              "entryType": measures[i].entryType,
              "name": measures[i].name,
              "startTime": measures[i].startTime,
              "duration": measures[i].duration});
  }
} catch(e) {};
try {
  performance.clearMarks();
  performance.clearMeasures();
} catch(e) {};
return m;
})()
