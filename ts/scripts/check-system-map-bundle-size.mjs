import { runDistGzipCheck } from "./bundle-size-check.mjs";

runDistGzipCheck({
  distFile: "dashboard/system-map/index.html",
  label: "system map dashboard",
  budgetKb: 30,
});
