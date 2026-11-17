/**
 * Icon System - Lucide Icons
 * Centralized icon registry with consistent sizing and styling
 */

import * as Lucide from 'lucide-static';

// Icon size constants
export const ICON_SIZE = {
  xs: 16,
  sm: 20,
  md: 24,
  lg: 32,
  xl: 48,
};

// Icon stroke width
export const ICON_STROKE = 2;

// Icon map - semantic names mapped to Lucide icons
export const ICONS = {
  // Navigation
  'nav-how': 'BookOpen',
  'nav-why': 'Brain',
  'nav-use-cases': 'FileText',
  'nav-security': 'Shield',
  'nav-about': 'Info',
  'nav-pricing': 'Tag',
  'nav-contact': 'Mail',
  'nav-privacy': 'Lock',

  // Actions
  'cta-primary': 'ArrowRight',
  'cta-secondary': 'Play',
  'cta-demo': 'PlayCircle',

  // Proof card
  'proof-verdict': 'AlertTriangle',
  'proof-warning': 'AlertTriangle',
  'proof-success': 'CheckCircle',
  'proof-recalculate': 'RotateCcw',
  'proof-delta': 'ArrowUp',

  // Pipeline steps
  'pipeline-1': 'Upload',
  'pipeline-2': 'Search',
  'pipeline-3': 'FileText',
  'pipeline-4': 'Cpu',
  'pipeline-5': 'Shield',
  'pipeline-6': 'UserCheck',

  // Use cases
  'use-case-gov': 'Building',
  'use-case-insurance': 'FileText',
  'use-case-bonus': 'DollarSign',

  // Security
  'security-host': 'Server',
  'security-zero': 'Shield',
  'security-audit': 'ClipboardList',

  // Actions
  'arrow-right': 'ArrowRight',
  'arrow-left': 'ArrowLeft',
  'chevron-down': 'ChevronDown',
  'chevron-up': 'ChevronUp',
  'chevron-left': 'ChevronLeft',
  'chevron-right': 'ChevronRight',
  'menu': 'Menu',
  'close': 'X',
  'check': 'Check',
  'check-circle': 'CheckCircle',
  'alert-triangle': 'AlertTriangle',
  'alert-circle': 'AlertCircle',
  'info': 'Info',
  'warning': 'AlertTriangle',
  'success': 'CheckCircle',
  'error': 'XCircle',
  'loader': 'Loader2',
  'lock': 'Lock',
  'unlock': 'Unlock',
  'shield': 'Shield',
  'search': 'Search',
  'mail': 'Mail',
  'calendar': 'Calendar',
  'clock': 'Clock',
  'download': 'Download',
  'upload': 'Upload',
  'eye': 'Eye',
  'eye-off': 'EyeOff',
  'settings': 'Settings',
  'user': 'User',
  'users': 'Users',
  'building': 'Building',
  'file': 'File',
  'file-text': 'FileText',
  'folder': 'Folder',
  'shield': 'Shield',
  'lock': 'Lock',
  'globe': 'Globe',
  'cpu': 'Cpu',
  'database': 'Database',
  'server': 'Server',
  'network': 'Network',
  'cloud': 'Cloud',
  'git-branch': 'GitBranch',
  'git-commit': 'GitCommit',
  'github': 'Github',
  'linkedin': 'Linkedin',
  'twitter': 'Twitter',
  'email': 'Mail',
  'phone': 'Phone',
  'map-pin': 'MapPin',
  'clock': 'Clock',
  'calendar': 'Calendar',
  'star': 'Star',
  'heart': 'Heart',
  'thumbs-up': 'ThumbsUp',
  'thumbs-down': 'ThumbsDown',
  'flag': 'Flag',
  'bookmark': 'Bookmark',
  'share': 'Share',
  'copy': 'Copy',
  'edit': 'Edit',
  'trash': 'Trash2',
  'save': 'Save',
  'print': 'Printer',
  'refresh': 'RefreshCw',
  'rotate-ccw': 'RotateCcw',
  'rotate-cw': 'RotateCw',
  'expand': 'Maximize2',
  'minimize': 'Minimize2',
  'fullscreen': 'Maximize2',
  'minimize-fullscreen': 'Minimize2',
  'zoom-in': 'ZoomIn',
  'zoom-out': 'ZoomOut',
  'home': 'Home',
  'menu': 'Menu',
  'x': 'X',
  'plus': 'Plus',
  'minus': 'Minus',
  'plus-circle': 'PlusCircle',
  'minus-circle': 'MinusCircle',
  'check': 'Check',
  'x': 'X',
  'chevron-down': 'ChevronDown',
  'chevron-up': 'ChevronUp',
  'chevron-left': 'ChevronLeft',
  'chevron-right': 'ChevronRight',
};

/**
 * Get SVG string for an icon
 * @param {string} name - Icon name from ICONS map
 * @param {Object} options - Options for the icon
 * @param {number} options.size - Icon size in pixels
 * @param {string} options.strokeWidth - Stroke width
 * @param {string} options.color - Color (CSS variable or hex)
 * @param {string} options.className - Additional CSS classes
 * @returns {string} SVG string
 */
export function getIcon(name, options = {}) {
  const iconName = ICONS[name];
  if (!iconName) {
    console.warn(`Icon "${name}" not found in registry`);
    return '';
  }

  const icon = Lucide[iconName];
  if (!icon) {
    console.warn(`Lucide icon "${iconName}" not found`);
    return '';
  }

  const {
    size = 24,
    strokeWidth = 2,
    color = 'currentColor',
    className = '',
    ...attrs
  } = options;

  // Lucide icons are arrays of [tag, attrs, children]
  const [tag, attrs, children] = icon;

  const svgAttrs = {
    width: attrs.width || size,
    height: attrs.height || size,
    viewBox: attrs.viewBox || '0 0 24 24',
    fill: 'none',
    stroke: color,
    'stroke-width': strokeWidth,
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    'aria-hidden': 'true',
    class: `lucide lucide-${iconName.toLowerCase()} ${className}`,
    ...attrs,
  };

  // Build SVG string
  const attrStr = Object.entries(svgAttrs)
    .map(([k, v]) => `${k}="${v}"`)
    .join(' ');

  // Build children
  const childrenStr = children
    ? children.map(([childTag, childAttrs]) => {
        const childAttrStr = Object.entries(childAttrs)
          .map(([k, v]) => `${k}="${v}"`)
          .join(' ');
        return `<${childTag} ${childAttrStr} />`;
      }).join('')
    : '';

  return `<svg ${attrStr}>${childrenStr}</svg>`;
}

/**
 * Create an icon element for direct DOM insertion
 * @param {string} name - Icon name from ICONS map
 * @param {Object} options - Options for the icon
 * @returns {SVGElement}
 */
export function createIconElement(name, options = {}) {
  const svgString = getIcon(name, options);
  const parser = new DOMParser();
  const doc = parser.parseFromString(svgString, 'image/svg+xml');
  return doc.documentElement;
}

/**
 * Replace all [data-icon] elements with Lucide icons
 * @param {Element} root - Root element to search within (default: document)
 */
export function replaceIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => {
    const name = el.getAttribute('data-icon');
    const size = el.getAttribute('data-size') || 24;
    const strokeWidth = el.getAttribute('data-stroke-width') || 2;
    const color = el.getAttribute('data-color') || 'currentColor';
    const className = el.getAttribute('data-class') || '';

    const svg = getIcon(name, { size, strokeWidth, color, className });
    if (svg) {
      el.innerHTML = svg;
    }
  });
}

// Auto-initialize on DOM ready
if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    replaceIcons();
  });
}

export default { ICONS, getIcon, createIconElement, replaceIcons, ICON_SIZE, ICON_STROKE };