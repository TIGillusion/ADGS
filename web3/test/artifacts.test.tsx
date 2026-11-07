import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactList } from "@/components/ArtifactList";

describe("产出物阅读与复制", () => {
  const paths = Array.from({length:7}, (_, i) => `D:\\项目\\研究\\round34\\成果${i}.md`);
  it("默认只显示四个文件，展开后可访问全部原始路径", () => {
    render(<ArtifactList paths={paths}/>);
    expect(screen.getAllByTitle("复制完整路径")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", {name:"查看全部 7 个文件"}));
    expect(screen.getAllByTitle("复制完整路径")).toHaveLength(7);
    expect(screen.getByTitle(paths[6])).toHaveTextContent(paths[6]);
    fireEvent.click(screen.getByRole("button", {name:"收起文件列表"}));
    expect(screen.queryByTitle(paths[6])).not.toBeInTheDocument();
  });
  it("复制保留中文及 Windows 反斜杠，提供成功反馈", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText}});
    render(<ArtifactList paths={[paths[0]]}/>);
    fireEvent.click(screen.getByRole("button", {name:"复制路径：成果0.md"}));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(paths[0]));
    expect(screen.getByRole("status")).toHaveTextContent("已复制完整路径");
  });
  it("剪贴板不可用时保留完整路径并提示手动复制", async () => {
    Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:vi.fn().mockRejectedValue(new Error("denied"))}});
    render(<ArtifactList paths={[paths[0]]}/>);
    fireEvent.click(screen.getByRole("button", {name:"复制路径：成果0.md"}));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("复制失败"));
    expect(screen.getByTitle(paths[0])).toHaveTextContent(paths[0]);
  });
});
