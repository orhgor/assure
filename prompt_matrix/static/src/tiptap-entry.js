import { Editor, Node, Extension, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

window.AssureTiptap = {
  Editor: Editor,
  Node: Node,
  Extension: Extension,
  mergeAttributes: mergeAttributes,
  StarterKit: StarterKit,
  Plugin: Plugin,
  PluginKey: PluginKey,
  Decoration: Decoration,
  DecorationSet: DecorationSet,
};
