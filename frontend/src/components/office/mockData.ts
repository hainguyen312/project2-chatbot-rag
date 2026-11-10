import type { Passage } from "./director";

/**
 * Dữ liệu MOCK cho văn phòng 3D — chưa phải dữ liệu Pháp Điển thật.
 * Khi ghép backend (Milvus / MySQL / Neo4j), bật chế độ Live và file này không còn được dùng.
 */

export type ToolName = "vector_search" | "graph_traverse" | "web_search" | "get_full_article";

/** Một "vòng" agent: các tool gọi trong cùng iteration */
export type MockRound = { tool: ToolName; args: Record<string, unknown>; resultIdx: number[]; ms: number }[];

export type MockScenario = {
  id: string;
  match: RegExp;
  rounds: MockRound[];
  passages: Passage[];
  answer: string;
};

const NOTE = "\n\n(Dữ liệu mẫu — sẽ được thay bằng nội dung Pháp Điển thật khi kết nối backend.)";

export const SCENARIOS: MockScenario[] = [
  {
    id: "divorce",
    match: /ly hôn|ly dị|bỏ vợ|bỏ chồng/i,
    passages: [
      {
        mapc: "MOCK.HNGD.51",
        ten: "Điều 51. Quyền yêu cầu giải quyết ly hôn",
        tenchude: "Hôn nhân và gia đình",
        tendemuc: "Luật Hôn nhân và gia đình 2014",
        noidung:
          "1. Vợ, chồng hoặc cả hai người có quyền yêu cầu Tòa án giải quyết ly hôn.\n3. Chồng không có quyền yêu cầu ly hôn trong trường hợp vợ đang có thai, sinh con hoặc đang nuôi con dưới 12 tháng tuổi." +
          NOTE,
        score: 0.91,
      },
      {
        mapc: "MOCK.HNGD.56",
        ten: "Điều 56. Ly hôn theo yêu cầu của một bên",
        tenchude: "Hôn nhân và gia đình",
        tendemuc: "Luật Hôn nhân và gia đình 2014",
        noidung:
          "1. Khi vợ hoặc chồng yêu cầu ly hôn mà hòa giải tại Tòa án không thành thì Tòa án giải quyết cho ly hôn nếu có căn cứ về việc vợ, chồng có hành vi bạo lực gia đình hoặc vi phạm nghiêm trọng quyền, nghĩa vụ của vợ, chồng làm cho hôn nhân lâm vào tình trạng trầm trọng, đời sống chung không thể kéo dài, mục đích của hôn nhân không đạt được." +
          NOTE,
        score: 0.88,
      },
      {
        mapc: "MOCK.HNGD.55",
        ten: "Điều 55. Thuận tình ly hôn",
        tenchude: "Hôn nhân và gia đình",
        tendemuc: "Luật Hôn nhân và gia đình 2014",
        noidung:
          "Trong trường hợp vợ chồng cùng yêu cầu ly hôn, nếu xét thấy hai bên thật sự tự nguyện ly hôn và đã thỏa thuận về việc chia tài sản, việc trông nom, nuôi dưỡng, chăm sóc, giáo dục con… thì Tòa án công nhận thuận tình ly hôn." +
          NOTE,
        score: 0.74,
      },
      {
        mapc: "WEB.1",
        ten: "Hồ sơ, thủ tục ly hôn đơn phương (bài viết mẫu)",
        source: "web",
        url: "https://example.com/mock/thu-tuc-ly-hon",
        noidung: "Hồ sơ gồm đơn khởi kiện, giấy chứng nhận đăng ký kết hôn, giấy tờ tùy thân, giấy khai sinh của con, giấy tờ về tài sản chung…" + NOTE,
        score: 0.7,
      },
    ],
    rounds: [
      [{ tool: "vector_search", args: { query: "điều kiện ly hôn đơn phương" }, resultIdx: [0, 1], ms: 5000 }],
      [
        { tool: "graph_traverse", args: { mapcs: ["MOCK.HNGD.56"] }, resultIdx: [2], ms: 5500 },
        { tool: "web_search", args: { query: "thủ tục ly hôn đơn phương hồ sơ" }, resultIdx: [3], ms: 5500 },
      ],
      [{ tool: "get_full_article", args: { mapc: "MOCK.HNGD.56" }, resultIdx: [1], ms: 3500 }],
    ],
    answer:
      "Theo **Điều 56 Luật Hôn nhân và gia đình 2014**, Tòa án giải quyết cho ly hôn theo yêu cầu của một bên khi:\n\n" +
      "1. **Hòa giải tại Tòa án không thành**;\n" +
      "2. Có căn cứ về **bạo lực gia đình** hoặc **vi phạm nghiêm trọng quyền, nghĩa vụ** vợ chồng;\n" +
      "3. Hôn nhân lâm vào tình trạng trầm trọng, đời sống chung không thể kéo dài.\n\n" +
      "⚠️ Lưu ý (Điều 51): người chồng **không được** yêu cầu ly hôn khi vợ đang mang thai, sinh con hoặc nuôi con dưới 12 tháng tuổi.\n\n" +
      "**Hồ sơ cơ bản:** đơn khởi kiện, giấy chứng nhận đăng ký kết hôn, giấy tờ tùy thân, giấy khai sinh của con, giấy tờ tài sản chung (nếu có).",
  },
  {
    id: "wage",
    match: /lương|nợ lương|chậm lương|sa thải|hợp đồng lao động|công ty/i,
    passages: [
      {
        mapc: "MOCK.BLLD.97",
        ten: "Điều 97. Kỳ hạn trả lương",
        tenchude: "Lao động",
        tendemuc: "Bộ luật Lao động 2019",
        noidung:
          "Người sử dụng lao động phải trả lương cho người lao động đúng kỳ hạn đã thỏa thuận. Trường hợp vì lý do bất khả kháng mà không thể trả lương đúng hạn thì không được chậm quá 30 ngày; nếu trả chậm từ 15 ngày trở lên thì phải đền bù cho người lao động một khoản tiền ít nhất bằng số tiền lãi của số tiền trả chậm…" +
          NOTE,
        score: 0.9,
      },
      {
        mapc: "MOCK.BLLD.35",
        ten: "Điều 35. Quyền đơn phương chấm dứt hợp đồng lao động của người lao động",
        tenchude: "Lao động",
        tendemuc: "Bộ luật Lao động 2019",
        noidung:
          "2. Người lao động có quyền đơn phương chấm dứt hợp đồng lao động không cần báo trước trong trường hợp: … b) Không được trả đủ lương hoặc trả lương không đúng thời hạn, trừ trường hợp quy định tại khoản 4 Điều 97…" +
          NOTE,
        score: 0.82,
      },
      {
        mapc: "MOCK.BLLD.188",
        ten: "Điều 188. Trình tự, thủ tục giải quyết tranh chấp lao động cá nhân",
        tenchude: "Lao động",
        tendemuc: "Bộ luật Lao động 2019",
        noidung: "Tranh chấp lao động cá nhân phải được giải quyết thông qua thủ tục hòa giải của hòa giải viên lao động trước khi yêu cầu Hội đồng trọng tài lao động hoặc Tòa án giải quyết, trừ một số trường hợp…" + NOTE,
        score: 0.71,
      },
    ],
    rounds: [
      [{ tool: "vector_search", args: { query: "công ty chậm trả lương nghĩa vụ" }, resultIdx: [0], ms: 5000 }],
      [{ tool: "graph_traverse", args: { mapcs: ["MOCK.BLLD.97"] }, resultIdx: [1, 2], ms: 5500 }],
    ],
    answer:
      "Nếu công ty nợ lương 3 tháng, bạn có các hướng sau:\n\n" +
      "1. **Yêu cầu trả lương + tiền đền bù trả chậm** — theo **Điều 97 BLLĐ 2019**, trả chậm từ 15 ngày trở lên phải đền bù ít nhất bằng tiền lãi của số tiền trả chậm.\n" +
      "2. **Nghỉ việc không cần báo trước** — **Điều 35** cho phép người lao động đơn phương chấm dứt hợp đồng khi không được trả đủ hoặc đúng hạn.\n" +
      "3. **Khiếu nại / tranh chấp** — gửi đơn tới Thanh tra lao động; hoặc yêu cầu hòa giải viên lao động, Hội đồng trọng tài, Tòa án (**Điều 188**).\n\n" +
      "💡 Hãy giữ lại hợp đồng, bảng lương, tin nhắn/email xác nhận nợ lương làm chứng cứ.",
  },
  {
    id: "traffic",
    match: /mũ bảo hiểm|giao thông|vượt đèn|nồng độ cồn|xe máy|bằng lái|giấy phép lái xe/i,
    passages: [
      {
        mapc: "MOCK.ND168.MBH",
        ten: "Xử phạt người điều khiển xe mô tô không đội mũ bảo hiểm",
        tenchude: "Giao thông vận tải",
        tendemuc: "Nghị định 168/2024/NĐ-CP",
        noidung: "Phạt tiền từ 400.000 đồng đến 600.000 đồng đối với người điều khiển, người ngồi trên xe mô tô, xe gắn máy không đội mũ bảo hiểm hoặc đội mũ không cài quai đúng quy cách…" + NOTE,
        score: 0.9,
      },
      {
        mapc: "WEB.2",
        ten: "Mức phạt lỗi không đội mũ bảo hiểm từ 2025 (bài viết mẫu)",
        source: "web",
        url: "https://example.com/mock/muc-phat-mu-bao-hiem",
        noidung: "Từ ngày 01/01/2025, mức phạt lỗi không đội mũ bảo hiểm tăng lên 400.000 – 600.000 đồng…" + NOTE,
        score: 0.72,
      },
    ],
    rounds: [
      [
        { tool: "vector_search", args: { query: "phạt không đội mũ bảo hiểm" }, resultIdx: [0], ms: 4500 },
        { tool: "web_search", args: { query: "mức phạt mũ bảo hiểm mới nhất" }, resultIdx: [1], ms: 4500 },
      ],
    ],
    answer:
      "Theo **Nghị định 168/2024/NĐ-CP** (hiệu lực từ 01/01/2025), người điều khiển hoặc người ngồi trên xe mô tô, xe gắn máy **không đội mũ bảo hiểm** (hoặc đội nhưng không cài quai đúng quy cách) bị phạt **400.000 – 600.000 đồng**.",
  },
  {
    id: "marriage",
    match: /kết hôn|đăng ký kết hôn|người nước ngoài|lấy chồng|lấy vợ/i,
    passages: [
      {
        mapc: "MOCK.HNGD.8",
        ten: "Điều 8. Điều kiện kết hôn",
        tenchude: "Hôn nhân và gia đình",
        tendemuc: "Luật Hôn nhân và gia đình 2014",
        noidung: "1. Nam, nữ kết hôn với nhau phải tuân theo các điều kiện sau đây: a) Nam từ đủ 20 tuổi trở lên, nữ từ đủ 18 tuổi trở lên; b) Việc kết hôn do nam và nữ tự nguyện quyết định; c) Không bị mất năng lực hành vi dân sự; d) Việc kết hôn không thuộc một trong các trường hợp cấm kết hôn…" + NOTE,
        score: 0.86,
      },
      {
        mapc: "MOCK.HT.38",
        ten: "Thủ tục đăng ký kết hôn có yếu tố nước ngoài",
        tenchude: "Tư pháp",
        tendemuc: "Luật Hộ tịch 2014",
        noidung: "Hai bên nộp tờ khai đăng ký kết hôn, giấy xác nhận của tổ chức y tế, giấy tờ chứng minh tình trạng hôn nhân… cho cơ quan đăng ký hộ tịch có thẩm quyền." + NOTE,
        score: 0.84,
      },
    ],
    rounds: [
      [{ tool: "vector_search", args: { query: "đăng ký kết hôn có yếu tố nước ngoài" }, resultIdx: [1], ms: 5000 }],
      [{ tool: "graph_traverse", args: { mapcs: ["MOCK.HT.38"] }, resultIdx: [0], ms: 4500 }],
      [{ tool: "get_full_article", args: { mapc: "MOCK.HT.38" }, resultIdx: [1], ms: 3500 }],
    ],
    answer:
      "Đăng ký kết hôn với người nước ngoài gồm các bước chính:\n\n" +
      "1. **Đáp ứng điều kiện kết hôn** (Điều 8 Luật HN&GĐ): nam từ đủ 20 tuổi, nữ từ đủ 18 tuổi, tự nguyện, không thuộc trường hợp cấm.\n" +
      "2. **Chuẩn bị hồ sơ**: tờ khai, giấy khám sức khỏe, giấy xác nhận tình trạng hôn nhân, hộ chiếu (giấy tờ nước ngoài cần hợp pháp hóa lãnh sự và dịch).\n" +
      "3. **Nộp tại cơ quan đăng ký hộ tịch có thẩm quyền** theo Luật Hộ tịch, hai bên cùng có mặt khi trao giấy chứng nhận.",
  },
];

/** Kịch bản chung khi câu hỏi pháp luật không khớp chủ đề nào ở trên */
export function genericScenario(q: string): MockScenario {
  return {
    id: "generic",
    match: /.*/,
    passages: [
      {
        mapc: "MOCK.GEN.1",
        ten: `Điều luật mẫu liên quan tới: "${q.slice(0, 50)}"`,
        tenchude: "Chủ đề mẫu",
        noidung: "Đây là đoạn điều luật giả lập để minh hoạ cách thủ thư mang tài liệu về bàn luật sư." + NOTE,
        score: 0.8,
      },
      {
        mapc: "MOCK.GEN.2",
        ten: "Điều luật mẫu được tham chiếu",
        tenchude: "Chủ đề mẫu",
        noidung: "Đoạn điều luật giả lập tìm được nhờ lần theo quan hệ THAM_CHIẾU trên đồ thị." + NOTE,
        score: 0.7,
      },
    ],
    rounds: [
      [{ tool: "vector_search", args: { query: q }, resultIdx: [0], ms: 5000 }],
      [{ tool: "graph_traverse", args: { mapcs: ["MOCK.GEN.1"] }, resultIdx: [1], ms: 4500 }],
    ],
    answer:
      `Văn phòng đã tra cứu câu hỏi **"${q}"**.\n\n` +
      "Đây là **câu trả lời mẫu** của chế độ Mock — chưa dùng dữ liệu thật. Hãy thử các câu hỏi gợi ý (ly hôn, nợ lương, mũ bảo hiểm, kết hôn với người nước ngoài) để xem kịch bản đầy đủ hơn.\n\n" +
      "Khi bật **Live**, câu trả lời sẽ được tổng hợp từ Milvus + Neo4j + Pháp Điển.",
  };
}

export const GREETING = /^(xin chào|chào|hello|hi|alo|hey)\b|cảm ơn|thank/i;
export const SPAM = /(https?:\/\/\S+.*){2,}|(.)\2{7,}|kiếm tiền online|casino|cá cược/i;
export const ANGRY = /bực|tức|điên|lừa đảo|vô dụng|chán quá|tệ quá|bức xúc/i;
export const OUT_OF_SCOPE = /thời tiết|bóng đá|nấu ăn|công thức|bài hát|chứng khoán|giá vàng|xổ số/i;
