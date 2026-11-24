"""Safe public copy for canonical EyeViora services with damaged legacy text."""


CANONICAL_DESCRIPTIONS = {
    "MAT_KHAM_TQ": "Khám và tư vấn mắt tổng quát. Giá tham khảo cho lượt khám; bác sĩ chỉ ghi nhận kỹ thuật bổ sung khi thực tế có chỉ định và thực hiện.",
    "MAT_KHAM_KHUC_XA": "Khám và tư vấn tật khúc xạ. Không tự động cộng riêng kỹ thuật vào hóa đơn nếu đã nằm trong nội dung lượt khám.",
    "MAT_KHAM_TRE_EM": "Khám và tư vấn mắt trẻ em. Giá tham khảo; kỹ thuật phát sinh chỉ tính khi bác sĩ xác nhận thực hiện riêng.",
    "MAT_KHAM_KHO_MAT": "Khám và tư vấn khô mắt. Test chuyên biệt, nếu được chỉ định và thực hiện riêng, được ghi nhận thành dịch vụ phát sinh.",
    "MAT_KHAM_GLOCOM": "Khám và tư vấn glôcôm. Các kỹ thuật chẩn đoán chuyên biệt chỉ tính riêng khi bác sĩ xác nhận đã thực hiện.",
    "MAT_KHAM_VONG_MAC": "Khám và tư vấn bệnh lý võng mạc. Chẩn đoán hình ảnh chuyên biệt chỉ tính riêng khi bác sĩ xác nhận đã thực hiện.",
    "MAT_DO_THI_LUC": "Kỹ thuật theo chỉ định; bác sĩ ghi nhận khi thực tế thực hiện. Không tự động cộng vào lượt khám.",
    "MAT_DO_KHUC_XA": "Kỹ thuật theo chỉ định; cần xác minh thiết bị và nhân sự từng chi nhánh trước triển khai thực tế.",
    "MAT_DO_NHAN_AP": "Kỹ thuật theo chỉ định; cần xác minh thiết bị và nhân sự từng chi nhánh trước triển khai thực tế.",
    "MAT_TEST_KHO_MAT": "Kỹ thuật theo chỉ định; bác sĩ chỉ ghi nhận khi thực tế phát sinh và thực hiện.",
    "MAT_DO_THI_TRUONG": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh.",
    "MAT_CHUP_OCT": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh.",
    "MAT_CHUP_DAY_MAT": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh.",
    "MAT_BAN_DO_GIAC_MAC": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh.",
    "MAT_SIEU_AM": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh.",
}


def catalog_description(code, stored_description):
    """Return canonical copy only when the stored Vietnamese text is visibly corrupted."""
    description = (stored_description or "").strip()
    canonical = CANONICAL_DESCRIPTIONS.get(code)
    if canonical and description.count("?") >= 3:
        return canonical
    return description
