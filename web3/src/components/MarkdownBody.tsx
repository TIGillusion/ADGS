import { memo, useState, type ComponentProps } from 'react';
import ReactMarkdown, { type Components, type ExtraProps } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';

type TextTree = {value?:string; children?:TextTree[]};
const plainText = (node: TextTree): string => node.value ?? node.children?.map(plainText).join('') ?? '';

function CodeBlock({node,children,...props}: ComponentProps<'pre'> & ExtraProps) {
  const [copied,setCopied]=useState(false);
  const [failed,setFailed]=useState(false);
  const code=node?.children.find(child=>child.type==='element'&&child.tagName==='code');
  const language=code?.type==='element' ? /language-([\w+-]+)/.exec(String(code.properties.className||''))?.[1] : undefined;
  const copy=async()=>{
    try {
      await navigator.clipboard.writeText(node ? plainText(node).replace(/\n$/,'') : '');
      setCopied(true);setFailed(false);
    } catch {setFailed(true);}
  };
  return <div className="markdown-code-block">
    <div className="markdown-code-toolbar"><span>{language||'代码'}</span><button type="button" onClick={()=>void copy()} aria-label="复制代码">{copied?'已复制':failed?'复制失败，点击重试':'复制代码'}</button></div>
    <pre {...props}>{children}</pre>
  </div>;
}

const components: Components = {
  pre: CodeBlock,
  table: ({node:_node,children,...props})=><div className="markdown-table" role="region" aria-label="表格" tabIndex={0}><table {...props}>{children}</table></div>,
  a: ({node:_node,href,children,...props})=><a {...props} href={href} target={/^https?:\/\//i.test(href||'')?'_blank':undefined} rel="noopener noreferrer">{children}</a>,
  img: ({node:_node,...props})=><img {...props} loading="lazy" referrerPolicy="no-referrer"/>,
};
const remarkPlugins = [remarkGfm,remarkMath];
const rehypePlugins = [
  [rehypeKatex,{strict:'ignore',trust:false,maxExpand:1000,errorColor:'#d99678'}],
  [rehypeHighlight,{detect:false,ignoreMissing:true}],
] satisfies NonNullable<ComponentProps<typeof ReactMarkdown>['rehypePlugins']>;

export const MarkdownBody = memo(function MarkdownBody({text,className=''}:{text?:string;className?:string}) {
  return <div className={`markdown-body ${className}`}><ReactMarkdown skipHtml remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>{text||''}</ReactMarkdown></div>;
});
