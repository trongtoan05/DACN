(() => {
    const messageFor = (field) => {
        const validity = field.validity;
        if (validity.valueMissing) return 'Vui lòng điền thông tin này.';
        if (validity.typeMismatch && field.type === 'email') return 'Vui lòng nhập địa chỉ email hợp lệ.';
        if (validity.typeMismatch && field.type === 'url') return 'Vui lòng nhập đường dẫn hợp lệ.';
        if (validity.badInput && field.type === 'number') return 'Vui lòng nhập một số hợp lệ.';
        if (validity.tooShort) return `Vui lòng nhập ít nhất ${field.minLength} ký tự.`;
        if (validity.tooLong) return `Vui lòng nhập không quá ${field.maxLength} ký tự.`;
        if (validity.rangeUnderflow) return `Giá trị phải lớn hơn hoặc bằng ${field.min}.`;
        if (validity.rangeOverflow) return `Giá trị phải nhỏ hơn hoặc bằng ${field.max}.`;
        if (validity.stepMismatch) return 'Vui lòng nhập giá trị theo đúng bước quy định.';
        if (validity.patternMismatch) return 'Dữ liệu chưa đúng định dạng yêu cầu.';
        return 'Vui lòng kiểm tra lại thông tin đã nhập.';
    };

    document.addEventListener('invalid', (event) => {
        const field = event.target;
        if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement)) return;
        field.setCustomValidity(messageFor(field));
    }, true);

    const clearMessage = (event) => {
        const field = event.target;
        if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement) {
            field.setCustomValidity('');
        }
    };
    document.addEventListener('input', clearMessage, true);
    document.addEventListener('change', clearMessage, true);
})();
