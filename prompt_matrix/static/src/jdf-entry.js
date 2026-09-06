import { render, JDFViewer, embed, jdf } from "@uurtech/jdf";

globalThis.assureJdfRender = render;
globalThis.JDFViewer = JDFViewer;
globalThis.assureJdfEmbed = embed;
globalThis.jdf = jdf;
